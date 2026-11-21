"""라크무 로비 서버(new-rakum 저장소 server.py)의 전적 기록(matches.json) 읽기.

로비 서버는 방장이 게임을 시작할 때 {time, map, players, room, results:{}} 를 쓰고,
게임이 끝나 각 플레이어가 0x24FF 를 보내면 results[계정] = {code, score} 를 채운다.
결과코드: 1 승 / 2 패 / 3·4 무 (+0x80 이면 래더=랭크게임).

읽는 방법(둘 중 하나, config):
  RAKMU_MATCHES_FILE  NAS 볼륨의 rakmu-live/data/matches.json 을 직접 읽기 (같은 볼륨이면 권장)
  RAKMU_MATCHES_URL   http://<서버>:11225/matches.json
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("arena.rakmu_server")

RESULT_WL = {1: "win", 2: "lose", 3: "draw", 4: "draw"}
LADDER_BIT = 0x80


def code_wl(code):
    try:
        return RESULT_WL.get(int(code) & 0x7F)
    except (TypeError, ValueError):
        return None


class ServerRecord:
    def __init__(self, raw: dict):
        self.raw = raw
        self.time = parse_time(raw.get("time", ""))
        self.map = str(raw.get("map") or "")
        self.players = [str(p) for p in raw.get("players") or [] if p]
        self.results = {}
        self.ladder = False
        for acc, v in (raw.get("results") or {}).items():
            if isinstance(v, dict):
                wl = v.get("wl") or code_wl(v.get("code"))
                try:
                    if int(v.get("code", 0)) & LADDER_BIT:
                        self.ladder = True
                except (TypeError, ValueError):
                    pass
            else:
                wl = v if v in ("win", "lose", "draw") else None
            if wl:
                self.results[str(acc)] = wl

    @property
    def ref(self) -> str:
        """같은 경기 키. 서버 0.9017 부터는 기록마다 id 가 있고, 그 전 기록은 시간|참가자."""
        rid = self.raw.get("id")
        return f"id:{rid}" if rid else f"{self.raw.get('time', '')}|{','.join(self.players)}"


def parse_time(s: str):
    s = (s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(s[:19], fmt)
        except ValueError:
            pass
    return None


class RakmuServerSource:
    def __init__(self, file: str = "", url: str = "", cache_seconds: float = 5.0):
        self.file = file
        self.url = url
        self.cache_seconds = cache_seconds
        self._cache = (0.0, [])
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.file or self.url)

    def records(self, force: bool = False) -> list[ServerRecord]:
        now = time.monotonic()
        if not force and now - self._cache[0] < self.cache_seconds:
            return self._cache[1]
        raw = []
        try:
            if self.file and Path(self.file).is_file():
                raw = json.loads(Path(self.file).read_text(encoding="utf-8"))
            elif self.url:
                with urllib.request.urlopen(self.url, timeout=5) as r:
                    raw = json.loads(r.read().decode("utf-8"))
            elif self.file:
                raise OSError(f"파일 없음: {self.file}")
        except (OSError, ValueError) as e:
            log.warning("라크무 서버 전적을 읽지 못함: %s", e)
            self.last_error = str(e)
            return self._cache[1]
        self.last_error = ""
        recs = [ServerRecord(r) for r in raw if isinstance(r, dict)]
        self._cache = (now, recs)
        return recs

    def find(self, played_at: dt.datetime, known_names: list[str], n_humans: int,
             window_min: int) -> ServerRecord | None:
        """리플레이와 같은 경기: 시간 차이가 창 안이고, 알려진 이름이 모두 들어 있고, 인원이 같은 기록."""
        best, best_gap = None, None
        known = {n.lower() for n in known_names}
        for rec in self.records():
            if rec.time is None or len(rec.players) < 2:
                continue
            gap = abs((rec.time - played_at).total_seconds())
            if gap > window_min * 60:
                continue
            names = {p.lower() for p in rec.players}
            if not known <= names:
                continue
            if n_humans and len(rec.players) != n_humans:
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = rec, gap
        return best
