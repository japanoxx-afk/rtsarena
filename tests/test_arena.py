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
from unittest import mock
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

    def test_result_uses_code_not_stored_wl(self):
        """2026-11-22 NAS: 예전 서버가 점수로 매긴 "wl": "draw" 가 파일에 남아 있었다. 서버 랭킹은 코드로 다시 판정한다."""
        raw = [{"time": "2026-11-21 03:08:46", "map": "[2]Distant.rkm", "players": ["aa", "zz"],
                "results": {"aa": {"code": 1, "score": 0, "wl": "draw"}, "zz": {"code": 2, "score": 0, "wl": "draw"}}},
               {"id": "s1", "time": "2026-11-21 04:00:00", "map": "m", "players": ["aa", "zz"],      # 코드 없음 → 점수
                "results": {"aa": {"score": 5}, "zz": {"score": 2}}}]
        st = new_store(self.tmp, raw)
        st.import_rakmu_records()
        lb = {r["name"]: (r["w"], r["l"], r["d"]) for r in st.leaderboard("rakmu")}
        self.assertEqual(lb, {"aa": (2, 0, 0), "zz": (0, 2, 0)})

    def test_playback_recordings_are_not_new_matches(self):
        """런처가 다시보기를 녹화해 올린다: 같은 시드, 늦은 녹화 시각 → 새 경기가 아니다 (지금 받을 때와 예전 데이터 정리 둘 다)."""
        raw = [{"id": "g1", "time": "2026-11-21 20:00:00", "map": "[2]Distant", "players": ["aa", "bb"],
                "results": {"aa": {"code": 1}, "bb": {"code": 2}}}]
        real = make_rkr([(0, "aa"), (1, "bb")], [32, 32] + [0] * 6, seed=777, date="2026-11-21T20:00:04", duration=600)
        other = make_rkr([(1, "bb"), (0, "aa")], [32, 32] + [0] * 6, seed=777, date="2026-11-21T20:00:05", duration=590)
        plays = [make_rkr([(0, "aa"), (1, "bb")], [32, 32] + [0] * 6, seed=777, date=f"2026-11-21T{h}:00", duration=d)
                 for h, d in (("20:40", 120), ("21:10", 600), ("22:30", 30))]
        st = new_store(self.tmp, raw)
        st.import_rakmu_records()
        self.assertEqual(st.ingest("rakmu", real, "a.rkr", "aa").status, "merged")
        self.assertEqual(st.ingest("rakmu", other, "b.rkr", "bb").status, "merged")      # 상대 리플레이는 합친다
        for i, data in enumerate(plays):
            self.assertEqual(st.ingest("rakmu", data, f"p{i}.rkr", "aa").status, "duplicate")
        self.assertEqual(st.match_rows("rakmu")[0], 1)
        self.assertEqual(len(st.match_rows("rakmu")[1][0]["replays"]), 2)

        # 예전 코드로 이미 받아 둔 다시보기: 서버 경기에 끼어든 것 + 따로 생긴 경기 → 정리
        st2 = new_store(tempfile.mkdtemp(), raw)
        st2._original_of_playback = lambda *a: None
        st2.ingest("rakmu", plays[0], "p0.rkr", "aa")          # 진짜보다 먼저 와서 서버 경기(20:00)에 붙음
        st2.ingest("rakmu", real, "a.rkr", "aa")
        st2.ingest("rakmu", plays[2], "p2.rkr", "aa")          # 22:30 → 새 경기
        del st2._original_of_playback
        st2.db.run("UPDATE replays SET seed=NULL, rec_date=NULL")   # 열이 생기기 전 DB
        self.assertEqual(st2.import_rakmu_records()["playbacks_removed"], 2)   # 동기화할 때 정리된다
        total, rows = st2.match_rows("rakmu")
        self.assertEqual(total, 1)
        m = rows[0]
        self.assertEqual((m["server_ref"], m["duration"], len(m["replays"])), ("id:g1", 600, 1))
        self.assertEqual({p["name"]: p["result"] for p in m["players"]}, {"aa": "win", "bb": "lose"})
        self.assertEqual(len(list((Path(st2.replay_dir)).rglob("*.rkr"))), 1)      # 파일도 지움
        self.assertEqual(st2.cleanup_playbacks(), 0)


def make_screplay(names, multiplayer=True, end="2026-11-25_20-25-00", ticks=30 * 600, mapname="MAP\\Duel.map",
                  diagnostic=False):
    """samchun tools/replay_archive.py create() 와 같은 모양의 .screplay (내용물은 가짜)."""
    import hashlib
    import zipfile
    save, journal = b"\x53\x02\x00\x00\x53\x02\x00\x00" + b"s" * 64, b"journal" * 50
    meta = {"format": 3, "kind": "samchun-replay", "auto_saved": True, "multiplayer": multiplayer,
            "participants": [{"slot": i, "id": n} for i, n in enumerate(names)], "slot_ids": list(range(len(names))),
            "first_tick": 2, "last_tick": 2 + ticks, "tick_rate": 30, "command_count": 10,
            "completion": "diagnostic" if diagnostic else "completed", "diagnostic_only": diagnostic,
            "checkpoint_sha256": hashlib.sha256(save).hexdigest(), "journal_sha256": hashlib.sha256(journal).hexdigest(),
            "maps": [{"path": mapname, "member": "maps/0.map", "sha256": "", "size": 4}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("metadata.json", json.dumps(meta, ensure_ascii=False))
        z.writestr("checkpoint.sav", save)
        z.writestr("record.journal", journal)
        z.writestr("maps/0.map", b"map!")
    return buf.getvalue(), f"{end}_auto.screplay"


# 삼국지천명2 호환 서버(samchun server/compat_server.py 1.3.58) data/matches.json 과 같은 모양
SAMCHUN_RAW = [
    {"id": "a1", "time": "2026-11-25 20:00:00", "room": "한판", "map": "Duel", "players": ["Alpha", "Bravo"],
     "results": {"Alpha": {"code": 1, "at": "2026-11-25 20:20:00"}, "Bravo": {"code": 2, "at": "2026-11-25 20:20:01"}}},
    {"id": "a2", "time": "2026-11-25 21:00:00", "room": "두판", "map": "", "players": ["Alpha", "Bravo"],
     "results": {"Alpha": {"code": 1, "at": "x"}, "Bravo": {"code": 2, "at": "x"}}},
    {"id": "a3", "time": "2026-11-25 22:00:00", "room": "", "map": "", "players": ["Bravo", "Delta"],
     "results": {"Bravo": {"code": 1, "at": "x"}, "Delta": {"code": 2, "at": "x"}}},
    {"id": "a4", "time": "2026-11-25 23:00:00", "room": "", "map": "", "players": ["Delta", "Alpha"],
     "results": {"Delta": {"code": 3, "at": "x"}, "Alpha": {"code": 3, "at": "x"}}},
    {"id": "a5", "time": "2026-11-25 23:10:00", "room": "혼자", "map": "", "players": ["Solo"],
     "results": {"Solo": {"code": 1, "at": "x"}}},                                   # 혼자: 제외
    {"id": "a6", "time": "2026-11-25 23:30:00", "room": "진행 중", "map": "", "players": ["Alpha", "Delta"], "results": {}},
]
# 같은 기록으로 호환 서버 ranking.json 이 내는 순서 (점수, 승, 패, 무)
SAMCHUN_SERVER_RANKING = [("Alpha", 1020, 2, 0, 1), ("Bravo", 990, 1, 2, 0), ("Delta", 990, 0, 1, 1)]


class SamchunTests(unittest.TestCase):
    """드롭박스 삼국지천명2: 호환 서버 matches.json → 리더보드(= 게임 안 랭킹)·전적기록."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_import_and_leaderboard_equals_in_game_ranking(self):
        db = Database(Path(self.tmp) / "a.sqlite3")
        st = Store(db, Path(self.tmp) / "replays", None, 15, {"samchun": FakeSource(SAMCHUN_RAW)})
        self.assertEqual(st.import_rakmu_records()["created"], 0)          # 라크무는 설정 안 됨 → 아무것도 안 함
        s = st.import_server_records("samchun")
        self.assertEqual((s["created"], s["results"], s["mismatch"]), (5, 4, []))
        self.assertEqual(st.import_server_records("samchun")["created"], 0)
        lb = [(r["name"], r["points"], r["w"], r["l"], r["d"]) for r in st.leaderboard("samchun")]
        self.assertEqual(lb, SAMCHUN_SERVER_RANKING)
        self.assertEqual(st.leaderboard("rakmu"), [])                     # 게임끼리 섞이지 않는다
        total, rows = st.match_rows("samchun")
        self.assertEqual(total, 5)
        self.assertEqual(rows[-1]["map_name"], "Duel")
        self.assertEqual(rows[-1]["room"], "한판")
        self.assertEqual(rows[-2]["map_name"], "맵 정보 없음")
        self.assertIsNone(rows[0]["result_source"])                        # 결과 대기 중인 경기
        self.assertEqual(st.basic_stats("samchun")["head"]["draws"], 1)
        db.conn.close()

    def test_replay_upload_links_to_server_match(self):
        db = Database(Path(self.tmp) / "a.sqlite3")
        st = Store(db, Path(self.tmp) / "replays", None, 15, {"samchun": FakeSource(SAMCHUN_RAW)})
        st.import_server_records("samchun")
        # 20:00 에 시작해 10분 한 Alpha vs Bravo (서버 기록 a1) — 두 사람이 각자 올린다
        a = make_screplay(["Alpha", "Bravo"], end="2026-11-25_20-10-30")
        b = make_screplay(["Bravo", "Alpha"], end="2026-11-25_20-10-31")
        r1, r2 = st.ingest("samchun", *a), st.ingest("samchun", *b)
        self.assertEqual((r1.status, r2.status), ("merged", "merged"))
        self.assertEqual(r1.match_hash, r2.match_hash)
        self.assertEqual(st.ingest("samchun", *a).status, "duplicate")
        m = st.match("samchun", r1.match_hash)
        self.assertEqual(m["server_ref"], "id:a1")
        self.assertEqual(len(m["replays"]), 2)
        self.assertEqual({p["name"]: p["result"] for p in m["players"]}, {"Alpha": "win", "Bravo": "lose"})
        self.assertEqual(m["map_name"], "Duel")
        self.assertEqual(int(m["duration"]), 600)
        # 싱글·진단·손상 파일은 거절
        self.assertEqual(st.ingest("samchun", *make_screplay(["Alpha"], multiplayer=False)).status, "rejected")
        self.assertEqual(st.ingest("samchun", *make_screplay(["Alpha", "Bravo"], diagnostic=True)).status, "rejected")
        self.assertEqual(st.ingest("samchun", b"PK\x03\x04junk", "x.screplay").status, "rejected")
        # 서버 기록이 아직 없는 경기 → 리플레이로 먼저 생기고, 나중 서버 기록이 같은 경기에 붙는다
        late = make_screplay(["Echo", "Fox"], end="2026-11-26_01-10-00")
        r3 = st.ingest("samchun", *late)
        self.assertEqual(r3.status, "created")
        st.sources["samchun"].raw = SAMCHUN_RAW + [
            {"id": "z9", "time": "2026-11-26 01:00:05", "room": "", "map": "", "players": ["Fox", "Echo"],
             "results": {"Fox": {"code": 1}, "Echo": {"code": 2}}}]
        s = st.import_server_records("samchun")
        self.assertEqual((s["created"], s["linked"]), (0, 1))
        m = st.match("samchun", r3.match_hash)
        self.assertEqual({p["name"]: p["result"] for p in m["players"]}, {"Echo": "lose", "Fox": "win"})
        self.assertEqual([r["name"] for r in st.leaderboard("samchun")][-1], "Echo")
        db.conn.close()

    def test_pages(self):
        path = Path(self.tmp) / "matches.json"
        path.write_text(json.dumps(SAMCHUN_RAW, ensure_ascii=False), encoding="utf-8")
        os.environ.update({"ARENA_DATA_DIR": self.tmp, "ARENA_PORT": "0", "ARENA_HOST": "127.0.0.1",
                           "RAKMU_MATCHES_FILE": "", "RAKMU_MATCHES_URL": "",
                           "SAMCHUN_MATCHES_FILE": str(path), "SAMCHUN_MATCHES_URL": ""})
        try:
            httpd, store = make_server(Config())
        finally:
            os.environ["SAMCHUN_MATCHES_FILE"] = ""
        self.assertIsNone(store.source("rakmu"))
        store.import_server_records("samchun")
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            def get(p):
                with urllib.request.urlopen(url + p, timeout=5) as r:
                    return r.status, r.read().decode()
            code, html = get("/samchun/")
            self.assertEqual(code, 200)
            self.assertIn('<option value="samchun" selected>삼국지천명2</option>', html)
            self.assertLess(html.index("Alpha"), html.index("Bravo"))
            self.assertIn("1020", html)
            self.assertIn('href="/samchun/matches"', html)                 # 메뉴가 삼국지천명2 주소로 바뀜
            code, html = get("/samchun/matches")
            self.assertIn("리플레이 없음", html)
            self.assertNotIn('name="race"', html)                          # 종족 정보 없음 → 종족 필터 없음
            self.assertIn("방 한판", html)
            self.assertNotIn("Solo", html)
            for p in ("/samchun/stats", "/samchun/maps", "/samchun/player/Alpha", "/samchun/map/Duel",
                      "/samchun/matches?player=Delta", "/api/samchun/matches.json", "/rakmu/", "/rakmu/stats"):
                self.assertEqual(get(p)[0], 200, p)
            self.assertIn("시간대별 경기 수", get("/samchun/stats")[1])
            self.assertIn('<option value="rakmu">라크무</option>', get("/samchun/stats")[1])
        finally:
            httpd.shutdown()
            httpd.server_close()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update({"ARENA_DATA_DIR": self.tmp, "ARENA_PORT": "0", "ARENA_HOST": "127.0.0.1",
                           "ARENA_UPLOAD_TOKEN": "test-token", "RAKMU_MATCHES_FILE": "", "RAKMU_MATCHES_URL": "",
                           "RAKMU_LAUNCHER_VERSION_URL": ""})   # 테스트에서는 GitHub 에 접속하지 않는다
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

    def test_install_download_tab(self):
        code, body, _ = self.get("/rakmu/download")
        html = body.decode()
        self.assertEqual(code, 200)
        self.assertIn('href="https://akansas.synology.me:5001/sharing/1AywKfMKA"', html)
        self.assertIn('href="/rakmu/download" class=active', html)
        self.assertNotIn("최신 런처", html)                                         # 버전 주소를 비우면 숨김
        self.assertIn('href="/rakmu/download"', self.get("/rakmu/")[1].decode())   # 모든 페이지 상단 탭

    def test_discord_button_on_every_page(self):
        for p in ("/rakmu/", "/rakmu/matches", "/rakmu/download", "/iop/"):
            html = self.get(p)[1].decode()
            self.assertIn('class="discord" href="https://discord.gg/YsYpTuHCh" target="_blank" rel="noopener"', html, p)
        from arena.web import discord_link
        cfg = self.httpd.RequestHandlerClass.cfg
        for bad in ("", "https://evil.example/x", "http://discord.gg/abc", 'https://discord.gg/a"><script>'):
            with mock.patch.object(cfg, "discord_url", bad):
                self.assertEqual(discord_link(cfg), "", bad)

    def test_latest_launcher_button_and_redirect(self):
        from arena.launcher_info import LauncherInfo
        ver = {"launcher_version": "0.9097",
               "launcher_url": "https://raw.githubusercontent.com/japanoxx-afk/new-rakum/main/RhakMuLauncher_v0.9097.exe",
               "launcher_size": 39356221, "launcher_sha256": "28" * 32}
        li = LauncherInfo("https://example/version.json", Path(self.tmp, "launcher_latest.json"),
                          fetch=lambda u: json.dumps(ver).encode())
        self.assertTrue(li.refresh())
        self.httpd.RequestHandlerClass.launcher = li
        self.httpd.RequestHandlerClass.cfg.rakmu_launcher_version_url = "https://example/version.json"
        html = self.get("/rakmu/download")[1].decode()
        self.assertIn("최신 런처 받기", html)
        self.assertIn("v0.9097 · 37.5 MB", html)
        self.assertIn("28" * 32, html)
        opener = urllib.request.build_opener(type("NoRedir", (urllib.request.HTTPRedirectHandler,),
                                                  {"redirect_request": lambda *a, **k: None}))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(self.url + "/rakmu/download/launcher", timeout=5)
        self.assertEqual((cm.exception.code, cm.exception.headers["Location"]), (302, ver["launcher_url"]))
        # 새 버전이 나오면 따라간다
        ver.update(launcher_version="0.9098", launcher_url=ver["launcher_url"].replace("9097", "9098"))
        self.assertTrue(li.refresh())
        self.assertIn("v0.9098", self.get("/rakmu/download")[1].decode())
        # 재시작해도 마지막 값 유지, 받기 실패 시 기존 값 유지
        def boom(u):
            raise OSError("offline")
        li2 = LauncherInfo("https://example/version.json", Path(self.tmp, "launcher_latest.json"), fetch=boom)
        self.assertFalse(li2.refresh())
        self.assertEqual(li2.get()["version"], "0.9098")

    def test_launcher_info_rejects_foreign_urls(self):
        from arena.launcher_info import validate
        good = {"launcher_version": "0.9097",
                "launcher_url": "https://raw.githubusercontent.com/japanoxx-afk/new-rakum/main/a.exe"}
        self.assertIsNotNone(validate(good))
        for bad_url in ("https://evil.example/a.exe", "http://raw.githubusercontent.com/japanoxx-afk/new-rakum/main/a.exe",
                        "https://raw.githubusercontent.com/japanoxx-afk/new-rakum/../x/a.exe",
                        "https://raw.githubusercontent.com/japanoxx-afk/new-rakum/main/a.exe\"><script>"):
            self.assertIsNone(validate(dict(good, launcher_url=bad_url)), bad_url)
        self.assertIsNone(validate(dict(good, launcher_version="<b>")))

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


# 태조왕건 더미서버(wanggun dummyserver.py 2.0) data/matches.json 과 같은 모양
WANGGUN_RAW = [
    {"id": "w1", "time": "2026-11-27 20:30:00", "started": "2026-11-27 20:00:00", "room": "즐겜방", "map": "고려벌판.map",
     "players": ["왕건", "견훤"],
     "results": {"견훤": {"code": 2, "at": "2026-11-27 20:30:00"}, "왕건": {"code": 1, "at": "2026-11-27 20:30:01"}}},
    {"id": "w2", "time": "2026-11-27 21:30:00", "started": "2026-11-27 21:00:00", "room": "2판", "map": "",
     "players": ["왕건", "궁예"],
     "results": {"왕건": {"code": 2, "at": "x", "dis": True}, "궁예": {"code": 1, "at": "x"}}},     # Dis = 패
    {"id": "w3", "time": "2026-11-27 22:00:00", "started": "2026-11-27 21:50:00", "room": "혼자", "map": "",
     "players": ["궁예"], "results": {"궁예": {"code": 1, "at": "x"}}},                            # 혼자: 제외
]
# 같은 기록으로 더미서버 ranking.json(게임 안 순위)이 내는 값 (이름, 점수, 승, 패, 무)
WANGGUN_SERVER_RANKING = [("궁예", 1010, 1, 0, 0), ("왕건", 1000, 1, 1, 0), ("견훤", 990, 0, 1, 0)]


class WanggunTests(unittest.TestCase):
    """드롭박스 태조왕건: 더미서버 matches.json → 리더보드(= 게임 안 순위)·전적기록."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_import_and_leaderboard(self):
        db = Database(Path(self.tmp) / "a.sqlite3")
        st = Store(db, Path(self.tmp) / "replays", None, 15, {"wanggun": FakeSource(WANGGUN_RAW)})
        s = st.import_server_records("wanggun")
        self.assertEqual((s["created"], s["results"], s["mismatch"]), (2, 2, []))
        lb = {r["name"]: (r["points"], r["w"], r["l"], r["d"]) for r in st.leaderboard("wanggun")}
        self.assertEqual(lb, {n: (p, w, l, d) for n, p, w, l, d in WANGGUN_SERVER_RANKING})
        self.assertEqual([r["name"] for r in st.leaderboard("wanggun")], ["궁예", "왕건", "견훤"])
        total, rows = st.match_rows("wanggun")
        self.assertEqual(total, 2)
        self.assertEqual(rows[-1]["map_name"], "고려벌판")
        self.assertEqual(rows[0]["map_name"], "맵 정보 없음")
        self.assertEqual(st.leaderboard("samchun"), [])
        db.conn.close()

    def test_pages_render_and_upload_rejected(self):
        from arena import web
        from arena.games import get
        db = Database(Path(self.tmp) / "a.sqlite3")
        st = Store(db, Path(self.tmp) / "replays", None, 15, {"wanggun": FakeSource(WANGGUN_RAW)})
        st.import_server_records("wanggun")
        cfg = Config()
        game = get("wanggun")
        self.assertTrue(game.ready)
        html = web.page_matches(cfg, st, game, {})
        self.assertIn("리플레이 미지원", html)
        self.assertIn("고려벌판", html)
        self.assertIn("github.com/japanoxx-afk/wanggun/releases", web.page_download(cfg, game))
        self.assertEqual(st.ingest("wanggun", b"junk", "x.bin").status, "rejected")
        db.conn.close()
