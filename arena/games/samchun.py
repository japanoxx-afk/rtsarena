"""삼국지천명2 (3KD2 1.20g) — 인터넷 호환 서버(japanoxx-afk/samchun server/compat_server.py) 전적 + 런처 리플레이(.screplay).

경기·승패는 호환 서버가 쓰는 matches.json 에서 온다
(라크무 로비 서버 matches.json 과 같은 형식 — arena/rakmu_server.py ServerRecord 로 그대로 읽는다):

  [{"id": "<경기 id>", "time": "YYYY-MM-DD HH:MM:SS", "room": "방 제목", "map": "맵 이름 또는 빈 값",
    "players": ["계정", ...], "results": {"계정": {"code": 1|2|3, "at": "..."}}}]

결과코드: 1 승 / 2 패 / 3 무. 원본 게임의 4(짧은 경기·무효)는 서버가 내보내지 않는다.
players = 게임 중계(TCP 4901)에 들어온 계정 → 혼자 만든 방은 사람 1명이라 사이트가 가져오지 않는다.
종족 정보는 서버가 알 수 없어 없다 (RACES 가 비어 있으면 화면에서 종족 칸·필터·통계를 숨긴다).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field

KEY = "samchun"
TITLE = "삼국지천명2"
EXT = ".screplay"
HAS_REPLAYS = True           # 런처 1.3.58 부터 자동 업로드 (src/ArenaUpload.cs)
MAX_REPLAY_BYTES = 64 * 1024 * 1024   # 리플레이 엔진 한도: 기록 128MB 지만 실제 대전은 1~5MB + 동봉 지도(최대 32MB)
RULE_NOTE = "삼국지천명2 게임 안 랭킹과 같은 규칙"

RACES: tuple = ()
RACE_COLORS: tuple = ()
RACE_LETTERS: tuple = ()
UNKNOWN_MAP = "맵 정보 없음"


def race_name(race) -> str:
    return "-"


def map_display(path: str) -> str:
    """서버가 방 정보에서 찾은 맵 파일 이름(확장자 없음). 못 찾았으면 빈 값 → '맵 정보 없음'."""
    name = (path or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if name.lower().endswith(".map"):
        name = name[:-4]
    return name or UNKNOWN_MAP


def map_catalog() -> list[dict]:
    return []


def map_info(name: str):
    return None


def map_image(name: str) -> str:
    return ""


# ---------------------------------------------------------------- 리플레이 (.screplay)
# samchun tools/replay_archive.py create(): 압축 안 한 ZIP
#   metadata.json  {"format": 2|3, "kind": "samchun-replay", "multiplayer": bool (네트워크 경기),
#                   "participants": [{"slot": n, "id": "계정"}], "first_tick", "last_tick", "tick_rate",
#                   "completion": "completed"|"stopped"|"diagnostic", "diagnostic_only", "maps": [{"path": "MAP\\x.map"}],
#                   "checkpoint_sha256", "journal_sha256", ...}
#   checkpoint.sav, record.journal, (형식 3) maps/<n>.map
# 파일 이름 = 저장(경기 끝) 시각(한국 시간) "YYYY-MM-DD_HH-MM-SS_auto[_stopped].screplay".
# participants 의 id = 게임이 네트워크 명단에서 복사한 이름 = 호환 서버 계정 이름.

class ReplayError(ValueError):
    """리플레이를 받을 수 없음 (메시지는 런처에 그대로 보여 준다)."""


@dataclass
class PlayerInfo:
    index: int
    name: str
    race: int | None = None


@dataclass
class ReplayInfo:
    start: dt.datetime            # 경기 시작 (끝 시각 - 길이)
    end: dt.datetime
    map_path: str
    duration: float
    multiplayer: bool
    completion: str
    players: list = field(default_factory=list)

    @property
    def map_name(self) -> str:
        return map_display(self.map_path)

    @property
    def date(self) -> str:
        return self.start.isoformat(timespec="seconds")


_NAME_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


def parse_replay(data: bytes, filename: str = "") -> ReplayInfo:
    if len(data) > MAX_REPLAY_BYTES:
        raise ReplayError("리플레이 파일이 너무 큽니다")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = {i.filename: i for i in z.infolist()}
        if not {"metadata.json", "checkpoint.sav", "record.journal"} <= set(names) or len(names) > 19:
            raise ReplayError("삼국지천명2 리플레이(.screplay) 형식이 아닙니다")
        if any(i.flag_bits & 1 for i in names.values()) or names["metadata.json"].file_size > 16384:
            raise ReplayError("리플레이 머리 정보가 올바르지 않습니다")
        meta = json.loads(z.read("metadata.json").decode("utf-8"))
        ok = (hashlib.sha256(z.read("checkpoint.sav")).hexdigest() == meta.get("checkpoint_sha256") and
              hashlib.sha256(z.read("record.journal")).hexdigest() == meta.get("journal_sha256"))
        saved = dt.datetime(*names["metadata.json"].date_time)
    except ReplayError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError, OSError) as e:
        raise ReplayError(f"리플레이를 읽을 수 없습니다: {type(e).__name__}") from None
    if not isinstance(meta, dict) or meta.get("kind") != "samchun-replay" or meta.get("format") not in (2, 3):
        raise ReplayError("지원하지 않는 리플레이 형식입니다 (런처 1.3.22 이상 자동 저장 리플레이만)")
    if not ok:
        raise ReplayError("리플레이 내용이 손상되었습니다 (체크섬 불일치)")
    if meta.get("diagnostic_only") or meta.get("completion") == "diagnostic":
        raise ReplayError("시험(진단) 기록은 저장하지 않습니다")
    try:
        rate = int(meta.get("tick_rate", 30))
        ticks = int(meta["last_tick"]) - int(meta["first_tick"])
    except (KeyError, TypeError, ValueError):
        raise ReplayError("리플레이 시간 정보가 없습니다") from None
    duration = max(0.0, ticks / rate) if 1 <= rate <= 240 else 0.0
    m = _NAME_TIME.search(filename or "")
    if m:
        try:
            saved = dt.datetime(*map(int, m.groups()))
        except ValueError:
            pass
    players, seen = [], set()
    for row in meta.get("participants") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("id") or "").strip()[:40]
        slot = row.get("slot")
        if not name or not isinstance(slot, int) or name.lower() in seen or any(ord(c) < 32 for c in name):
            continue
        seen.add(name.lower())
        players.append(PlayerInfo(slot, name))
    maps = meta.get("maps") or []
    map_path = str(maps[0].get("path") or "") if maps and isinstance(maps[0], dict) else ""
    return ReplayInfo(start=saved - dt.timedelta(seconds=int(duration)), end=saved, map_path=map_path,
                      duration=duration, multiplayer=bool(meta.get("multiplayer")),
                      completion=str(meta.get("completion") or ""), players=players)


def is_multiplayer(info: ReplayInfo) -> bool:
    """네트워크 경기이고 이름 있는 사람이 2명 이상. 싱글(나 + 컴퓨터)은 저장하지 않는다."""
    return info.multiplayer and len(info.players) >= 2
