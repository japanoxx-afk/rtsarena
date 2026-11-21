"""리플레이 접수 → 경기(전적) 만들기, 로비 서버 결과 반영, 페이지용 조회."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from . import games
from .db import Database
from .games import rakmu
from .rakmu_server import RakmuServerSource

POINTS_BASE, POINTS_WIN, POINTS_LOSE = 1000, 10, 10   # 라크무 서버 게임 내 랭킹과 같은 규칙


@dataclass
class UploadResult:
    ok: bool
    status: str        # created | merged | duplicate | rejected
    message: str
    match_hash: str = ""


def _safe_filename(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", s).strip(" ._")
    return (s or "replay")[:100]


def _iso_to_local(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s[:19])
    except ValueError:
        return None


class Store:
    def __init__(self, db: Database, replay_dir: Path, rakmu_source: RakmuServerSource | None = None,
                 window_minutes: int = 15):
        self.db = db
        self.replay_dir = replay_dir
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        self.rakmu = rakmu_source or RakmuServerSource()
        self.window = window_minutes

    # ------------------------------------------------------------------ 업로드
    def ingest(self, game_key: str, data: bytes, filename: str = "", uploader: str = "") -> UploadResult:
        game = games.get(game_key)
        if not game or not game.ready:
            return UploadResult(False, "rejected", f"지원하지 않는 게임: {game_key}")
        if game_key != "rakmu":
            return UploadResult(False, "rejected", "아직 해석기가 없는 게임입니다")
        sha = hashlib.sha256(data).hexdigest()
        row = self.db.one("SELECT m.hash FROM replays r JOIN matches m ON m.id=r.match_id WHERE r.sha256=?", (sha,))
        if row:
            return UploadResult(True, "duplicate", "이미 올라간 리플레이입니다", row["hash"])
        try:
            info = rakmu.parse_replay(data)
        except rakmu.ReplayError as e:
            return UploadResult(False, "rejected", str(e))
        if not rakmu.is_multiplayer(info):
            return UploadResult(False, "rejected", "상대 플레이어가 없는 경기(나 + 컴퓨터)는 저장하지 않습니다")
        played = _iso_to_local(info.date)
        if played is None:
            return UploadResult(False, "rejected", "리플레이 날짜를 읽을 수 없습니다")

        # 로비 서버 기록으로 이름이 빠진 상대를 채우고 승패를 가져온다
        known = [p.name for p in info.players if p.name]
        rec = None
        if self.rakmu.configured:
            rec = self.rakmu.find(played, known, info.human_slots, self.window)
        if rec:
            missing = [p for p in info.players if not p.name]
            rest = [n for n in rec.players if n.lower() not in {k.lower() for k in known}]
            for p, n in zip(missing, rest):
                p.name = n
        if sum(1 for p in info.players if p.name) < 2:
            return UploadResult(False, "rejected",
                                "상대 플레이어 이름을 확인할 수 없어 저장하지 않습니다 (로비 서버 기록 없음)")
        info.players = [p for p in info.players if p.name]

        results, source, gametype = {}, None, "normal"
        if rec and rec.results:
            by_name = {k.lower(): v for k, v in rec.results.items()}
            results = {p.index: by_name.get(p.name.lower()) for p in info.players}
            source = "server"
            gametype = "rank" if rec.ladder else "normal"
        if not any(results.values()):
            inferred = info.inferred_results()
            if inferred:
                results, source = inferred, "replay"

        fname = _safe_filename(filename or f"{played:%Y%m%d_%H%M%S}_{info.map_name}{rakmu.EXT}")
        if not fname.lower().endswith(rakmu.EXT):
            fname += rakmu.EXT
        with self.db.tx() as c:
            m = None
            if rec:   # 로비 서버 기록으로 이미 만들어 둔 경기가 있으면 거기에 붙인다
                m = c.execute("SELECT * FROM matches WHERE game=? AND server_ref=?", (game_key, rec.ref)).fetchone()
            if m is None:
                m = self._find_same_match(c, game_key, info, played)
            status = "merged" if m else "created"
            if m is None:
                mhash = hashlib.sha256(f"{game_key}|{info.seed}|{info.map_path}|{played.isoformat()}|{sha}".encode()).hexdigest()
                cur = c.execute(
                    "INSERT INTO matches(game,hash,seed,map_path,map_name,played_at,duration,gametype,result_source,server_ref)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (game_key, mhash, info.seed, info.map_path, info.map_name, played.strftime("%Y-%m-%d %H:%M:%S"),
                     info.duration, gametype, source, rec.ref if rec else None))
                mid = cur.lastrowid
                for p in info.players:
                    c.execute("INSERT INTO match_players(match_id,slot,name,race,result) VALUES(?,?,?,?,?)",
                              (mid, p.index, p.name, p.race, results.get(p.index)))
            else:
                mid, mhash = m["id"], m["hash"]
                # 같은 경기의 다른 시점 리플레이 또는 로비 서버 기록으로 먼저 만든 경기: 비어 있던 정보만 채움
                has_replay = c.execute("SELECT 1 FROM replays WHERE match_id=? LIMIT 1", (mid,)).fetchone()
                if not has_replay:      # 서버 기록만 있던 경기: 맵·시드는 리플레이 쪽이 정확하다
                    c.execute("UPDATE matches SET map_path=?, map_name=?, seed=COALESCE(seed, ?) WHERE id=?",
                              (info.map_path, info.map_name, info.seed, mid))
                if info.duration > (m["duration"] or 0):
                    c.execute("UPDATE matches SET duration=? WHERE id=?", (info.duration, mid))
                # 승패: 서버 결과는 언제나 우선, 추정은 아무 결과도 없을 때만 (서버 결과를 추정으로 덮지 않음)
                set_results = source == "server" or (source == "replay" and not m["result_source"])
                if set_results:
                    c.execute("UPDATE matches SET result_source=?, gametype=? WHERE id=?", (source, gametype, mid))
                if rec and not m["server_ref"]:
                    c.execute("UPDATE matches SET server_ref=? WHERE id=?", (rec.ref, mid))
                self._merge_players(c, mid, info.players, results, set_results)
            rel = Path(game_key) / played.strftime("%Y%m") / f"{sha[:16]}{rakmu.EXT}"
            dest = self.replay_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            self._save_player_stats(c, mid, info, prefer=info.duration >= (m["duration"] or 0) if m else True)
            c.execute("INSERT INTO replays(match_id,sha256,filename,stored_path,size,duration,uploader)"
                      " VALUES(?,?,?,?,?,?,?)",
                      (mid, sha, fname, rel.as_posix(), len(data), info.duration, (uploader or "")[:32] or None))
        msg = "새 경기로 저장했습니다" if status == "created" else "같은 경기에 리플레이를 추가했습니다"
        return UploadResult(True, status, msg, mhash)

    def _find_same_match(self, c, game_key, info, played):
        lo = (played - dt.timedelta(minutes=self.window)).strftime("%Y-%m-%d %H:%M:%S")
        hi = (played + dt.timedelta(minutes=self.window)).strftime("%Y-%m-%d %H:%M:%S")
        rows = c.execute("SELECT * FROM matches WHERE game=? AND map_path=? AND played_at BETWEEN ? AND ?",
                         (game_key, info.map_path, lo, hi)).fetchall()
        names = {p.name.lower() for p in info.players}
        for m in rows:
            if info.seed is not None and m["seed"] is not None and m["seed"] != info.seed:
                continue
            theirs = {r[0].lower() for r in c.execute("SELECT name FROM match_players WHERE match_id=?", (m["id"],))}
            if names & theirs:
                return m
        return None

    @staticmethod
    def _merge_players(c, mid, players, results, set_results):
        """리플레이 참가자를 경기에 합친다: 이름이 같으면 종족·결과만 채우고, 없으면 새로 넣는다."""
        rows = {r["name"].lower(): r for r in c.execute("SELECT * FROM match_players WHERE match_id=?", (mid,))}
        used = {r["slot"] for r in rows.values()}
        for p in players:
            r = rows.get(p.name.lower())
            if r is not None:
                c.execute("UPDATE match_players SET race=COALESCE(race, ?) WHERE match_id=? AND slot=?",
                          (p.race, mid, r["slot"]))
                if set_results:
                    c.execute("UPDATE match_players SET result=? WHERE match_id=? AND slot=?",
                              (results.get(p.index), mid, r["slot"]))
            else:
                slot = p.index if p.index not in used else max(used) + 1
                used.add(slot)
                c.execute("INSERT INTO match_players(match_id,slot,name,race,result) VALUES(?,?,?,?,?)",
                          (mid, slot, p.name, p.race, results.get(p.index) if set_results else None))

    @staticmethod
    def _save_player_stats(c, mid, info, prefer=True):
        """리플레이 지표를 같은 이름의 참가자에 넣는다. 같은 경기 리플레이가 여럿이면 가장 긴 것(prefer)을 쓴다."""
        for p in info.players:
            st = info.player_stats.get(p.index)
            if not st or not p.name:
                continue
            cond = "" if prefer else " AND peak_army IS NULL"
            c.execute("UPDATE match_players SET peak_army=?, units_made=?, buildings_made=?"
                      " WHERE match_id=? AND name=? COLLATE NOCASE" + cond,
                      (st["peak_army"], st["units_made"], st["buildings_made"], mid, p.name))

    def backfill_player_stats(self) -> int:
        """지표 열이 생기기 전에 올라온 리플레이를 다시 읽어 채운다 (시작할 때 한 번)."""
        rows = self.db.q(
            "SELECT r.match_id, r.stored_path FROM replays r WHERE EXISTS (SELECT 1 FROM match_players p"
            " WHERE p.match_id=r.match_id AND p.peak_army IS NULL) ORDER BY r.match_id, r.duration DESC")
        done = 0
        for r in rows:
            try:
                info = rakmu.parse_replay((self.replay_dir / r["stored_path"]).read_bytes())
            except (OSError, rakmu.ReplayError):
                continue
            with self.db.tx() as c:
                self._save_player_stats(c, r["match_id"], info, prefer=False)
            done += 1
        return done

    # ------------------------------------------------------------------ 로비 서버와 동기화
    def import_rakmu_records(self) -> dict:
        """로비 서버 matches.json 의 모든 대전을 경기로 가져온다 (리플레이가 없어도).

        - 같은 경기 = server_ref(서버 기록 id, 옛 기록은 시간|참가자). 리플레이로 먼저 생긴 경기는 이름·시간으로 찾아 연결.
        - 승패는 서버 결과로 덮어쓴다. 리더보드는 서버 결과만 쓰므로 게임 안 랭킹(server.py ranking_rows)과 같아진다.
        """
        stats = {"created": 0, "linked": 0, "results": 0, "mismatch": []}
        if not self.rakmu.configured:
            return stats
        recs = self.rakmu.records(force=True)
        with self.db.tx() as c:
            for rec in recs:
                names = list(dict.fromkeys(rec.players))
                if len({n for n in names}) < 2 or rec.time is None:
                    continue
                m = c.execute("SELECT * FROM matches WHERE game='rakmu' AND server_ref=?", (rec.ref,)).fetchone()
                if m is None:
                    m = self._find_unlinked(c, rec, names)
                    if m is not None:
                        c.execute("UPDATE matches SET server_ref=? WHERE id=?", (rec.ref, m["id"]))
                        stats["linked"] += 1
                if m is None:
                    mhash = hashlib.sha256(f"rakmu|server|{rec.ref}".encode()).hexdigest()
                    cur = c.execute(
                        "INSERT INTO matches(game,hash,map_path,map_name,played_at,duration,gametype,server_ref)"
                        " VALUES('rakmu',?,?,?,?,0,?,?)",
                        (mhash, rec.map, rakmu.map_display(rec.map), rec.time.strftime("%Y-%m-%d %H:%M:%S"),
                         "rank" if rec.ladder else "normal", rec.ref))
                    for i, n in enumerate(names):
                        c.execute("INSERT INTO match_players(match_id,slot,name) VALUES(?,?,?)", (cur.lastrowid, i, n))
                    m = c.execute("SELECT * FROM matches WHERE id=?", (cur.lastrowid,)).fetchone()
                    stats["created"] += 1
                if rec.results:
                    rows = c.execute("SELECT slot, name, result FROM match_players WHERE match_id=?", (m["id"],)).fetchall()
                    by_name = {k.lower(): v for k, v in rec.results.items()}
                    want = {r["slot"]: by_name.get(r["name"].lower()) for r in rows}
                    if m["result_source"] != "server" or any(r["result"] != want[r["slot"]] for r in rows):
                        c.execute("UPDATE matches SET result_source='server', gametype=? WHERE id=?",
                                  ("rank" if rec.ladder else "normal", m["id"]))
                        for slot, wl in want.items():
                            c.execute("UPDATE match_players SET result=? WHERE match_id=? AND slot=?", (wl, m["id"], slot))
                        stats["results"] += 1
        stats["mismatch"] = self.ranking_mismatch(recs)
        return stats

    def _find_unlinked(self, c, rec, names):
        lo = (rec.time - dt.timedelta(minutes=self.window)).strftime("%Y-%m-%d %H:%M:%S")
        hi = (rec.time + dt.timedelta(minutes=self.window)).strftime("%Y-%m-%d %H:%M:%S")
        want = {n.lower() for n in names}
        for m in c.execute("SELECT * FROM matches WHERE game='rakmu' AND server_ref IS NULL AND played_at BETWEEN ? AND ?"
                           " ORDER BY played_at", (lo, hi)).fetchall():
            have = {r[0].lower() for r in c.execute("SELECT name FROM match_players WHERE match_id=?", (m["id"],))}
            if have == want:
                return m
        return None

    def ranking_mismatch(self, recs) -> list:
        """서버와 같은 방법(server.py compute_ranking)으로 센 승/패/무와 사이트 리더보드가 다른 계정 목록."""
        tally = {}
        for rec in recs:
            if len(set(rec.players)) < 2:
                continue
            for acc, wl in rec.results.items():
                if acc:
                    t = tally.setdefault(acc, {"win": 0, "lose": 0, "draw": 0})
                    t[wl] += 1
        site = {r["name"]: {"win": r["w"], "lose": r["l"], "draw": r["d"]} for r in self.leaderboard("rakmu")}
        return sorted(n for n in set(tally) | set(site) if tally.get(n) != site.get(n))

    sync_rakmu_results = import_rakmu_records   # 예전 이름

    # ------------------------------------------------------------------ 조회
    def match_rows(self, game: str, player: str = "", race: str = "", map_name: str = "",
                   gametype: str = "", page: int = 1, per_page: int = 20):
        where, args = ["m.game=?"], [game]
        if player:
            where.append("EXISTS (SELECT 1 FROM match_players x WHERE x.match_id=m.id AND x.name LIKE ? COLLATE NOCASE)")
            args.append(f"%{player}%")
        if race != "":
            where.append("EXISTS (SELECT 1 FROM match_players x WHERE x.match_id=m.id AND x.race=?)")
            args.append(int(race))
        if map_name:
            where.append("m.map_name=?")
            args.append(map_name)
        if gametype:
            where.append("m.gametype=?")
            args.append(gametype)
        w = " AND ".join(where)
        total = self.db.one(f"SELECT COUNT(*) n FROM matches m WHERE {w}", args)["n"]
        rows = self.db.q(f"SELECT m.* FROM matches m WHERE {w} ORDER BY m.played_at DESC, m.id DESC LIMIT ? OFFSET ?",
                         args + [per_page, (max(1, page) - 1) * per_page])
        return total, [self._with_players(r) for r in rows]

    def _with_players(self, m) -> dict:
        d = dict(m)
        d["players"] = [dict(p) for p in self.db.q(
            "SELECT * FROM match_players WHERE match_id=? ORDER BY slot", (m["id"],))]
        d["replays"] = [dict(r) for r in self.db.q(
            "SELECT id, filename, size, duration, uploader, uploaded_at FROM replays WHERE match_id=? ORDER BY duration DESC, id",
            (m["id"],))]
        return d

    def match(self, game: str, mhash: str):
        m = self.db.one("SELECT * FROM matches WHERE game=? AND hash=?", (game, mhash))
        return self._with_players(m) if m else None

    def replay_for_download(self, mhash: str, replay_id: int | None = None):
        m = self.db.one("SELECT * FROM matches WHERE hash=?", (mhash,))
        if not m:
            return None
        if replay_id:
            r = self.db.one("SELECT * FROM replays WHERE match_id=? AND id=?", (m["id"], replay_id))
        else:
            r = self.db.one("SELECT * FROM replays WHERE match_id=? ORDER BY duration DESC, id LIMIT 1", (m["id"],))
        if not r:
            return None
        self.db.run("UPDATE matches SET downloads=downloads+1 WHERE id=?", (m["id"],))
        return self.replay_dir / r["stored_path"], r["filename"]

    def maps(self, game: str) -> list:
        return [dict(r) for r in self.db.q(
            "SELECT map_name, COUNT(*) n, AVG(NULLIF(duration,0)) avg_dur, MAX(played_at) last,"
            " SUM(gametype='rank') ranked FROM matches WHERE game=? GROUP BY map_name ORDER BY n DESC, map_name", (game,))]

    def map_stats(self, game: str, map_name: str) -> dict:
        """맵 하나의 통계: 경기 수·평균 시간, 종족별 출전/승률, 1:1 종족 상성, 많이 이긴 플레이어, 최근 경기."""
        head = self.db.one(
            "SELECT COUNT(*) n, AVG(NULLIF(duration,0)) avg_dur, MAX(duration) max_dur, MIN(NULLIF(duration,0)) min_dur,"
            " MAX(played_at) last, SUM(gametype='rank') ranked FROM matches WHERE game=? AND map_name=?", (game, map_name))
        st = self.race_stats(game, map_name)
        top = [dict(r) for r in self.db.q(
            "SELECT p.name, COUNT(*) n, SUM(p.result='win') w, SUM(p.result='lose') l FROM match_players p"
            " JOIN matches m ON m.id=p.match_id WHERE m.game=? AND m.map_name=?"
            " GROUP BY p.name ORDER BY w DESC, n DESC, p.name LIMIT 8", (game, map_name))]
        recent = [self._with_players(m) for m in self.db.q(
            "SELECT * FROM matches WHERE game=? AND map_name=? ORDER BY played_at DESC, id DESC LIMIT 5", (game, map_name))]
        # 통계 지표(화면에서 종족·상대 종족으로 거른다): 참가자 1명 = 1줄. 상대 종족은 1:1 경기만.
        entries = [dict(r) for r in self.db.q(
            "SELECT p.race, p.result, m.duration dur, p.peak_army, p.units_made, p.buildings_made,"
            " (SELECT o.race FROM match_players o WHERE o.match_id=m.id AND o.slot!=p.slot) opp,"
            " (SELECT COUNT(*) FROM match_players c WHERE c.match_id=m.id) np"
            " FROM match_players p JOIN matches m ON m.id=p.match_id WHERE m.game=? AND m.map_name=?"
            " ORDER BY m.played_at DESC LIMIT 4000", (game, map_name))]
        for x in entries:
            if x.pop("np") != 2:
                x["opp"] = None
        mirror = {r["race"]: r["n"] for r in self.db.q(
            "SELECT a.race, COUNT(*) n FROM match_players a JOIN match_players b ON a.match_id=b.match_id AND a.slot<b.slot"
            " JOIN matches m ON m.id=a.match_id WHERE m.game=? AND m.map_name=? AND a.race=b.race"
            " AND (SELECT COUNT(*) FROM match_players c WHERE c.match_id=m.id)=2 GROUP BY a.race", (game, map_name))}
        return {"head": dict(head), "per": st["per"], "vs": st["vs"], "top": top, "recent": recent,
                "entries": entries, "mirror": mirror}

    def today_count(self, game: str) -> int:
        return self.db.one("SELECT COUNT(*) n FROM matches WHERE game=? AND date(played_at)=date('now','localtime')",
                           (game,))["n"]

    def leaderboard(self, game: str) -> list:
        """게임 안 랭킹과 같은 규칙: 로비 서버 결과가 있는 경기만, 계정 이름 그대로(대소문자 구분),
        점수 = 1000 + 승×10 − 패×10. 리플레이로 추정한 승패는 넣지 않는다."""
        rows = self.db.q(
            "SELECT p.name, SUM(p.result='win') w, SUM(p.result='lose') l, SUM(p.result='draw') d, COUNT(*) games,"
            " MAX(m.played_at) last FROM match_players p JOIN matches m ON m.id=p.match_id"
            " WHERE m.game=? AND m.result_source='server' AND p.result IN ('win','lose','draw') AND p.name!=''"
            " GROUP BY p.name", (game,))
        out = []
        for r in rows:
            d = dict(r)
            d["w"], d["l"], d["d"] = d["w"] or 0, d["l"] or 0, d["d"] or 0
            d["points"] = max(0, POINTS_BASE + POINTS_WIN * d["w"] - POINTS_LOSE * d["l"])
            decided = d["w"] + d["l"]
            d["rate"] = (100.0 * d["w"] / decided) if decided else None
            races = self.db.q("SELECT p.race, COUNT(*) n FROM match_players p JOIN matches m ON m.id=p.match_id"
                              " WHERE m.game=? AND p.name=? AND p.race IS NOT NULL GROUP BY p.race ORDER BY n DESC",
                              (game, d["name"]))
            d["main_race"] = races[0]["race"] if races else None
            out.append(d)
        out.sort(key=lambda r: (-r["points"], -r["w"], r["l"], r["name"]))   # server.py ranking_rows 와 같은 순서
        for i, r in enumerate(out, 1):
            r["rank"] = i
        return out

    def player(self, game: str, name: str):
        rows = self.leaderboard(game)
        return next((r for r in rows if r["name"] == name), None) or \
            next((r for r in rows if r["name"].lower() == name.lower()), None)

    def race_stats(self, game: str, map_name: str | None = None) -> dict:
        """종족별 승률 + 종족 상성표 (1:1 경기 기준). map_name 을 주면 그 맵만."""
        mf, ma = (" AND m.map_name=?", (map_name,)) if map_name is not None else ("", ())
        per = {r["race"]: dict(r) for r in self.db.q(
            "SELECT p.race, COUNT(*) n, SUM(p.result='win') w, SUM(p.result='lose') l, SUM(p.result='draw') d"
            " FROM match_players p JOIN matches m ON m.id=p.match_id WHERE m.game=?" + mf + " GROUP BY p.race", (game, *ma))}
        vs = {}
        for r in self.db.q(
                "SELECT a.race ra, b.race rb, SUM(a.result='win') w, SUM(a.result='lose') l FROM match_players a"
                " JOIN match_players b ON a.match_id=b.match_id AND a.slot!=b.slot JOIN matches m ON m.id=a.match_id"
                " WHERE m.game=? AND (SELECT COUNT(*) FROM match_players c WHERE c.match_id=m.id)=2" + mf +
                " GROUP BY a.race, b.race", (game, *ma)):
            vs[(r["ra"], r["rb"])] = (r["w"] or 0, r["l"] or 0)
        return {"per": per, "vs": vs}
