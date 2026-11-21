"""python -m unittest discover -s tests -v   (표준 라이브러리만, 네트워크는 127.0.0.1 만 사용)"""
import gzip
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "launcher" / "rakmu"))

from arena.config import Config  # noqa: E402
from arena.db import Database  # noqa: E402
from arena.games import rakmu  # noqa: E402
from arena.rakmu_server import RakmuServerSource  # noqa: E402
from arena.store import Store  # noqa: E402
from arena.web import make_server  # noqa: E402
import arena_upload  # noqa: E402


def make_rkr(players, slots, races=None, date="2026-11-21T20:00:00", seed=1234, mapname="DATA\\MAPS\\[2]Distant.rkm",
             final_buildings=None, duration=300.0, truncated=False) -> bytes:
    """players: [(index, name)]; final_buildings: {owner: count} 마지막 장면 건물 수."""
    head = {"format": 3, "date": date, "map": mapname, "map_rkm": "",
            "players": [{"index": i, "name": n, "race": (races or [0] * 8)[i]} for i, n in players],
            "types": {}, "local": players[0][0], "seed": seed, "options": "", "frame0": 1, "commands": True,
            "races": races or [0, 1, 3, 3, 3, 3, 3, 3], "slots": slots}
    units, uid = [], 1
    for owner, n in (final_buildings or {}).items():
        for _ in range(n):
            units.append([uid, 10, owner, 100, 100, 500, 1])
            uid += 1
    lines = [json.dumps(head), json.dumps({"t": 0.0, "k": [[900, 5, 0, 1, 1, 50, 0], [901, 5, 1, 1, 1, 50, 0]]}),
             json.dumps({"t": duration, "k": units})]
    raw = ("\n".join(lines) + "\n").encode()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as g:
        g.write(raw)
    data = buf.getvalue()
    return data[:-10] if truncated else data


class FakeSource(RakmuServerSource):
    def __init__(self, raw):
        super().__init__(file="x")
        self.raw = raw

    def records(self, force=False):
        from arena.rakmu_server import ServerRecord
        return [ServerRecord(r) for r in self.raw]


def new_store(tmp, raw=None):
    db = Database(Path(tmp) / "a.sqlite3")
    return Store(db, Path(tmp) / "replays", FakeSource(raw) if raw is not None else None, 15)


class ParseTests(unittest.TestCase):
    def test_single_vs_computer_is_not_multiplayer(self):
        info = rakmu.parse_replay(make_rkr([(0, "aa")], [32, 16, 0, 0, 0, 0, 0, 0]))
        self.assertEqual(info.human_slots, 1)
        self.assertEqual(info.computers, 1)
        self.assertFalse(rakmu.is_multiplayer(info))

    def test_two_humans_and_inferred_result(self):
        info = rakmu.parse_replay(make_rkr([(0, "aa"), (1, "bb")], [32, 32, 0, 0, 0, 0, 0, 0],
                                           final_buildings={0: 3, 1: 0}, duration=512.5))
        self.assertTrue(rakmu.is_multiplayer(info))
        self.assertEqual(info.duration, 512.5)
        self.assertEqual(info.map_name, "(2) Distant")
        self.assertEqual(info.inferred_results(), {0: "win", 1: "lose"})

    def test_garbage_rejected(self):
        with self.assertRaises(rakmu.ReplayError):
            rakmu.parse_replay(b"not a replay")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_single_player_rejected_and_not_stored(self):
        st = new_store(self.tmp)
        r = st.ingest("rakmu", make_rkr([(0, "aa")], [32, 16, 0, 0, 0, 0, 0, 0]), "x.rkr", "aa")
        self.assertFalse(r.ok)
        self.assertEqual(st.db.one("SELECT COUNT(*) n FROM matches")["n"], 0)
        self.assertEqual(list(Path(self.tmp, "replays").rglob("*.rkr")), [])

    def test_two_povs_merge_and_duplicate(self):
        st = new_store(self.tmp)
        a = make_rkr([(0, "aa"), (1, "bb")], [32, 32, 0, 0, 0, 0, 0, 0], final_buildings={0: 2, 1: 0})
        b = make_rkr([(0, "aa"), (1, "bb")], [32, 32, 0, 0, 0, 0, 0, 0], date="2026-11-21T20:00:01",
                     final_buildings={0: 2, 1: 0}, duration=301)
        r1 = st.ingest("rakmu", a, "a.rkr", "aa")
        r2 = st.ingest("rakmu", b, "b.rkr", "bb")
        r3 = st.ingest("rakmu", a, "a.rkr", "aa")
        self.assertEqual((r1.status, r2.status, r3.status), ("created", "merged", "duplicate"))
        self.assertEqual(r1.match_hash, r2.match_hash)
        total, rows = st.match_rows("rakmu")
        self.assertEqual(total, 1)
        self.assertEqual(len(rows[0]["replays"]), 2)
        self.assertEqual({p["name"]: p["result"] for p in rows[0]["players"]}, {"aa": "win", "bb": "lose"})
        self.assertEqual(rows[0]["result_source"], "replay")

    def test_server_record_fills_missing_name_and_results(self):
        raw = [{"time": "2026-11-21 19:59:50", "map": "[2]Distant", "players": ["aa", "상대"], "room": "r",
                "results": {"aa": {"code": 0x82, "score": -10}, "상대": {"code": 0x81, "score": 10}}}]
        st = new_store(self.tmp, raw)
        data = make_rkr([(0, "aa")], [32, 32, 0, 0, 0, 0, 0, 0], races=[1, 2, 3, 3, 3, 3, 3, 3])
        r = st.ingest("rakmu", data, "c.rkr", "aa")
        self.assertTrue(r.ok, r.message)
        m = st.match("rakmu", r.match_hash)
        self.assertEqual(m["gametype"], "rank")
        self.assertEqual(m["result_source"], "server")
        self.assertEqual({p["name"]: (p["race"], p["result"]) for p in m["players"]},
                         {"aa": (1, "lose"), "상대": (2, "win")})

    def test_missing_opponent_without_server_record_rejected(self):
        st = new_store(self.tmp, [])
        r = st.ingest("rakmu", make_rkr([(0, "aa")], [32, 32, 0, 0, 0, 0, 0, 0]), "d.rkr", "aa")
        self.assertFalse(r.ok)

    def test_sync_applies_late_server_results(self):
        raw = [{"time": "2026-11-21 20:00:05", "map": "m", "players": ["aa", "bb"], "results": {}}]
        st = new_store(self.tmp, raw)
        import datetime as dt
        now = dt.datetime.now().replace(microsecond=0)
        raw[0]["time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        r = st.ingest("rakmu", make_rkr([(0, "aa"), (1, "bb")], [32, 32, 0, 0, 0, 0, 0, 0], date=now.isoformat()), "e.rkr")
        self.assertIsNone(st.match("rakmu", r.match_hash)["result_source"])
        raw[0]["results"] = {"aa": {"code": 2}, "bb": {"code": 1}}
        s = st.import_rakmu_records()
        self.assertEqual((s["created"], s["results"], s["mismatch"]), (0, 1, []))
        m = st.match("rakmu", r.match_hash)
        self.assertEqual({p["name"]: p["result"] for p in m["players"]}, {"aa": "lose", "bb": "win"})
        lb = st.leaderboard("rakmu")
        self.assertEqual([(x["name"], x["points"]) for x in lb], [("bb", 1010), ("aa", 990)])


class RankingSyncTests(unittest.TestCase):
    """사이트 리더보드 = 게임 안 랭킹 (라크무 서버 matches.json 기준)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_all_server_games_imported_and_ranking_equals_server(self):
        raw = [
            {"id": "m1", "time": "2026-11-20 20:00:00", "map": "[2]Distant", "players": ["Aa", "bb"],
             "results": {"Aa": {"code": 1, "wl": "win"}, "bb": {"code": 2, "wl": "lose"}}},
            {"time": "2026-11-20 21:00:00", "map": "[2]Distant", "players": ["Aa", "cc"],          # id 없는 옛 기록
             "results": {"Aa": {"code": 0x82, "wl": "lose"}, "cc": {"code": 0x81, "wl": "win"}}},
            {"id": "m3", "time": "2026-11-20 22:00:00", "map": "m", "players": ["bb", "cc"],
             "results": {"bb": {"code": 3, "wl": "draw"}, "cc": {"code": 3, "wl": "draw"}}},
            {"id": "m4", "time": "2026-11-20 23:00:00", "map": "m", "players": ["solo"], "results": {}},  # 혼자: 제외
            {"id": "m5", "time": "2026-11-20 23:30:00", "map": "m", "players": ["bb", "cc"], "results": {}},  # 결과 없음
        ]
        st = new_store(self.tmp, raw)
        s = st.import_rakmu_records()
        self.assertEqual((s["created"], s["results"], s["mismatch"]), (4, 3, []))
        self.assertEqual(st.import_rakmu_records()["created"], 0)                          # 두 번 가져와도 그대로
        # 리플레이로만 추정한 경기는 랭킹에 안 들어간다
        st.ingest("rakmu", make_rkr([(0, "dd"), (1, "ee")], [32, 32] + [0] * 6, date="2026-11-19T10:00:00",
                                    final_buildings={0: 1, 1: 0}), "x.rkr")
        lb = {r["name"]: (r["points"], r["w"], r["l"], r["d"]) for r in st.leaderboard("rakmu")}
        self.assertEqual(lb, {"Aa": (1000, 1, 1, 0), "cc": (1010, 1, 0, 1), "bb": (990, 0, 1, 1)})
        self.assertEqual(st.ranking_mismatch(st.rakmu.records()), [])
        total, rows = st.match_rows("rakmu")
        self.assertEqual(total, 5)                                                         # 서버 4 + 리플레이 1
        self.assertTrue(all(not m["replays"] for m in rows if m["server_ref"]))
        rank = st.match_rows("rakmu", player="cc")[1]
        self.assertTrue(any(m["gametype"] == "rank" for m in rank))

    def test_replay_uploaded_after_server_import_joins_same_match(self):
        raw = [{"id": "g1", "time": "2026-11-21 20:00:03", "map": "[2]Distant", "players": ["aa", "bb"],
                "results": {"aa": {"code": 1, "wl": "win"}, "bb": {"code": 2, "wl": "lose"}}}]
        st = new_store(self.tmp, raw)
        st.import_rakmu_records()
        before = st.match_rows("rakmu")[1][0]
        # 리플레이 쪽 추정(bb 승)은 서버 결과를 덮지 않는다
        r = st.ingest("rakmu", make_rkr([(0, "bb"), (1, "aa")], [32, 32] + [0] * 6, races=[2, 1] + [3] * 6,
                                        final_buildings={0: 3, 1: 0}, duration=420), "g1.rkr", "bb")
        self.assertEqual((r.status, r.match_hash), ("merged", before["hash"]))
        m = st.match("rakmu", r.match_hash)
        self.assertEqual(st.match_rows("rakmu")[0], 1)
        self.assertEqual({p["name"]: (p["race"], p["result"]) for p in m["players"]},
                         {"aa": (1, "win"), "bb": (2, "lose")})
        self.assertEqual((m["duration"], m["map_name"], len(m["replays"])), (420, "(2) Distant", 1))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update({"ARENA_DATA_DIR": self.tmp, "ARENA_PORT": "0", "ARENA_HOST": "127.0.0.1",
                           "ARENA_UPLOAD_TOKEN": "test-token", "RAKMU_MATCHES_FILE": "", "RAKMU_MATCHES_URL": ""})
        self.httpd, self.store = make_server(Config())
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as r:
            return r.status, r.read(), r.headers

    def test_launcher_upload_pages_and_download(self):
        rdir = Path(self.tmp, "launcher_replays")
        rdir.mkdir()
        multi = make_rkr([(0, "용사"), (1, "bb")], [32, 32, 0, 0, 0, 0, 0, 0], final_buildings={0: 0, 1: 4})
        (rdir / "20261121_200000_[2]Distant.rkr").write_bytes(multi)
        (rdir / "20261121_210000_[2]Distant.rkr").write_bytes(make_rkr([(0, "용사")], [32, 16, 0, 0, 0, 0, 0, 0]))
        (rdir / "20261121_220000_[2]Distant.rkr").write_bytes(make_rkr([(0, "a"), (1, "b")], [32, 32] + [0] * 6,
                                                                        truncated=True))
        bad = arena_upload.ArenaUploader(self.tmp, rdir, self.url, "wrong")
        self.assertEqual(bad.scan_once(now=time.time() + 60)[0][1], "auth")
        up = arena_upload.ArenaUploader(self.tmp, rdir, self.url, "test-token", account_fn=lambda: "용사")
        rows = {name: status for name, status, _ in up.scan_once(now=time.time() + 60)}
        self.assertEqual(rows, {"20261121_200000_[2]Distant.rkr": "uploaded",
                                "20261121_210000_[2]Distant.rkr": "skipped"})   # 잘린 파일은 기록 중으로 보고 대기
        self.assertEqual(up.scan_once(now=time.time() + 60), [])               # 다시 올리지 않음

        code, body, _ = self.get("/rakmu/matches")
        html = body.decode()
        self.assertEqual(code, 200)
        self.assertIn("용사", html)
        self.assertIn("(2) Distant", html)
        self.assertIn("리플레이 ⬇", html)
        h = self.store.match_rows("rakmu")[1][0]["hash"]
        code, data, headers = self.get(f"/download/{h}")
        self.assertEqual(data, multi)
        self.assertIn("attachment", headers["Content-Disposition"])
        for p in ("/rakmu/", "/rakmu/stats", "/rakmu/maps", "/rakmu/player/%EC%9A%A9%EC%82%AC", "/iop/",
                  "/rakmu/matches?player=bb&race=1&gametype=normal", "/api/rakmu/matches.json"):
            self.assertEqual(self.get(p)[0], 200, p)
        self.assertEqual(self.store.match_rows("rakmu")[1][0]["downloads"], 1)

        # 맵통계: 미리보기 카드(경기 없는 맵 포함) + 맵별 통계 조각 + 그림 파일
        from arena.games import rakmu as rk
        html = self.get("/rakmu/maps")[1].decode()
        self.assertIn('data-nm="(2) Distant"', html)
        self.assertIn('data-nm="(8) X-ice"', html)               # 아직 경기가 없는 맵도 보인다
        img = rk.map_image("(2) Distant")
        self.assertTrue(img.startswith("/static/maps/rakmu/"))
        code, data, headers = self.get(img)
        self.assertEqual((code, headers["Content-Type"], data[:4]), (200, "image/png", b"\x89PNG"))
        frag = self.get("/rakmu/map/" + urllib.parse.quote("(2) Distant") + "?frag=1")[1].decode()
        self.assertIn("종족별 승률", frag)
        self.assertIn("총 진행게임 수", frag)
        self.assertIn("통계 지표", frag)
        self.assertIn("\"peak_army\": ", frag)          # 리플레이에서 뽑은 지표
        self.assertIn("용사", frag)
        self.assertIn("금광", frag)
        self.assertIsNotNone(self.store.db.one("SELECT peak_army FROM match_players WHERE name=?", ("용사",))[0])
        self.store.db.run("UPDATE match_players SET peak_army=NULL, units_made=NULL, buildings_made=NULL")
        self.assertEqual(self.store.backfill_player_stats(), 1)                  # 예전 리플레이 다시 읽기
        self.assertIsNotNone(self.store.db.one("SELECT units_made FROM match_players WHERE name=?", ("용사",))[0])
        self.assertEqual(self.get("/rakmu/map/" + urllib.parse.quote("(8) X-ice"))[0], 200)
        self.assertIn('class="mthumb"', self.get("/rakmu/matches")[1].decode())

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/nope/x")
        self.assertEqual(cm.exception.code, 404)


class DbRecoveryTests(unittest.TestCase):
    """2026-11-21 NAS: 트랜잭션이 열린 채 남아 동기화가 매번 실패하고, 잠금이 안 풀려 요청 스레드가 쌓였다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(Path(self.tmp) / "a.sqlite3")

    def tearDown(self):
        self.db.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_leftover_transaction_is_rolled_back(self):
        self.db.conn.execute("BEGIN")          # 실패한 COMMIT 뒤처럼 열린 채 남김
        with self.db.tx() as c:
            c.execute("INSERT INTO matches(game,hash,map_path,map_name,played_at) VALUES('rakmu','h1','m','m','2026-01-01 00:00:00')")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM matches")[0], 1)
        self.assertFalse(self.db.conn.in_transaction)

    def test_failed_begin_releases_lock(self):
        import threading
        class Boom:
            in_transaction = False
            def execute(self, sql, *a):
                raise sqlite3.OperationalError("boom")
        real = self.db.conn
        self.db.conn = Boom()
        with self.assertRaises(sqlite3.OperationalError):
            with self.db.tx():
                pass
        self.db.conn = real
        got = []
        t = threading.Thread(target=lambda: got.append(self.db.lock.acquire(timeout=1)))
        t.start(); t.join()
        self.assertEqual(got, [True])          # 다른 스레드가 잠금을 잡을 수 있어야 한다

    def test_error_in_body_rolls_back(self):
        with self.assertRaises(ValueError):
            with self.db.tx() as c:
                c.execute("INSERT INTO matches(game,hash,map_path,map_name,played_at) VALUES('rakmu','h2','m','m','2026-01-01 00:00:00')")
                raise ValueError
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM matches")[0], 0)
        self.assertFalse(self.db.conn.in_transaction)

    def test_commit_locked_reopens_without_locks(self):
        real = self.db.conn
        class Locked:
            @property
            def in_transaction(self):
                return real.in_transaction
            def execute(self, sql, *a):
                if sql == "COMMIT":
                    raise sqlite3.OperationalError("database is locked")
                return real.execute(sql, *a)
            def close(self):
                real.close()
        self.db.conn = Locked()
        with self.assertRaises(sqlite3.OperationalError):
            with self.db.tx():
                pass
        self.assertTrue(self.db.nolock)
        with self.db.tx() as c:
            c.execute("INSERT INTO matches(game,hash,map_path,map_name,played_at) VALUES('rakmu','h3','m','m','2026-01-01 00:00:00')")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM matches")[0], 1)

    def test_missing_matches_file_is_reported(self):
        from arena.rakmu_server import RakmuServerSource
        src = RakmuServerSource(Path(self.tmp) / "nope.json", None)
        self.assertEqual(src.records(force=True), [])
        self.assertIn("nope.json", src.last_error)

if __name__ == "__main__":
    unittest.main()
