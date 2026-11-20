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
                # 같은 경기의 다른 시점 리플레이: 비어 있던 정보만 채움
                if info.duration > (m["duration"] or 0):
                    c.execute("UPDATE matches SET duration=? WHERE id=?", (info.duration, mid))
                if source == "server" or (source and not m["result_source"]):
                    c.execute("UPDATE matches SET result_source=?, gametype=? WHERE id=?", (source, gametype, mid))
                    for p in info.players:
                        c.execute("UPDATE match_players SET result=? WHERE match_id=? AND slot=?",
                                  (results.get(p.index), mid, p.index))
                if rec and not m["server_ref"]:
                    c.execute("UPDATE matches SET server_ref=? WHERE id=?", (rec.ref, mid))
                for p in info.players:
                    c.execute("INSERT OR IGNORE INTO match_players(match_id,slot,name,race,result) VALUES(?,?,?,?,?)",
                              (mid, p.index, p.name, p.race, results.get(p.index)))
            rel = Path(game_key) / played.strftime("%Y%m") / f"{sha[:16]}{rakmu.EXT}"
            dest = self.replay_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
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

    # ------------------------------------------------------------------ 결과 동기화
    def sync_rakmu_results(self, days: int = 14) -> int:
        """승패가 서버 기준이 아닌 최근 경기에 로비 서버 결과를 다시 붙인다. 바뀐 경기 수를 돌려준다."""
        if not self.rakmu.configured:
            return 0
        self.rakmu.records(force=True)
        since = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = self.db.q("SELECT * FROM matches WHERE game='rakmu' AND (result_source IS NULL OR result_source!='server')"
                         " AND played_at>=?", (since,))
        changed = 0
        for m in rows:
            players = self.db.q("SELECT * FROM match_players WHERE match_id=?", (m["id"],))
            played = dt.datetime.strptime(m["played_at"], "%Y-%m-%d %H:%M:%S")
            rec = self.rakmu.find(played, [p["name"] for p in players], len(players), self.window)
            if not rec or not rec.results:
                continue
            by_name = {k.lower(): v for k, v in rec.results.items()}
            if not any(by_name.get(p["name"].lower()) for p in players):
                continue
            with self.db.tx() as c:
                c.execute("UPDATE matches SET result_source='server', gametype=?, server_ref=COALESCE(server_ref, ?)"
                          " WHERE id=?", ("rank" if rec.ladder else "normal", rec.ref, m["id"]))
                for p in players:
                    c.execute("UPDATE match_players SET result=? WHERE match_id=? AND slot=?",
                              (by_name.get(p["name"].lower()), m["id"], p["slot"]))
            changed += 1
        return changed

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
            "SELECT map_name, COUNT(*) n, AVG(duration) avg_dur, MAX(played_at) last FROM matches WHERE game=?"
            " GROUP BY map_name ORDER BY n DESC, map_name", (game,))]

    def today_count(self, game: str) -> int:
        return self.db.one("SELECT COUNT(*) n FROM matches WHERE game=? AND date(played_at)=date('now','localtime')",
                           (game,))["n"]

    def leaderboard(self, game: str) -> list:
        rows = self.db.q(
            "SELECT p.name, SUM(p.result='win') w, SUM(p.result='lose') l, SUM(p.result='draw') d, COUNT(*) games,"
            " MAX(m.played_at) last FROM match_players p JOIN matches m ON m.id=p.match_id"
            " WHERE m.game=? GROUP BY p.name COLLATE NOCASE", (game,))
        out = []
        for r in rows:
            d = dict(r)
            d["w"], d["l"], d["d"] = d["w"] or 0, d["l"] or 0, d["d"] or 0
            d["points"] = max(0, POINTS_BASE + POINTS_WIN * d["w"] - POINTS_LOSE * d["l"])
            decided = d["w"] + d["l"]
            d["rate"] = (100.0 * d["w"] / decided) if decided else None
            races = self.db.q("SELECT p.race, COUNT(*) n FROM match_players p JOIN matches m ON m.id=p.match_id"
                              " WHERE m.game=? AND p.name=? COLLATE NOCASE GROUP BY p.race ORDER BY n DESC",
                              (game, d["name"]))
            d["main_race"] = races[0]["race"] if races else None
            out.append(d)
        out.sort(key=lambda r: (-r["points"], -r["w"], r["l"], r["name"].lower()))
        for i, r in enumerate(out, 1):
            r["rank"] = i
        return out

    def player(self, game: str, name: str):
        for r in self.leaderboard(game):
            if r["name"].lower() == name.lower():
                return r
        return None

    def race_stats(self, game: str) -> dict:
        """종족별 승률 + 종족 상성표 (1:1 경기 기준)."""
        per = {r["race"]: dict(r) for r in self.db.q(
            "SELECT p.race, COUNT(*) n, SUM(p.result='win') w, SUM(p.result='lose') l FROM match_players p"
            " JOIN matches m ON m.id=p.match_id WHERE m.game=? GROUP BY p.race", (game,))}
        vs = {}
        for r in self.db.q(
                "SELECT a.race ra, b.race rb, SUM(a.result='win') w, SUM(a.result='lose') l FROM match_players a"
                " JOIN match_players b ON a.match_id=b.match_id AND a.slot!=b.slot JOIN matches m ON m.id=a.match_id"
                " WHERE m.game=? AND (SELECT COUNT(*) FROM match_players c WHERE c.match_id=m.id)=2"
                " GROUP BY a.race, b.race", (game,)):
            vs[(r["ra"], r["rb"])] = (r["w"] or 0, r["l"] or 0)
        return {"per": per, "vs": vs}
