"""삼국지천명2 (3KD2 1.20g) — 인터넷 호환 서버(japanoxx-afk/samchun server/compat_server.py) 전적.

리플레이 업로드는 아직 없다. 경기·승패는 모두 호환 서버가 쓰는 matches.json 에서 온다
(라크무 로비 서버 matches.json 과 같은 형식 — arena/rakmu_server.py ServerRecord 로 그대로 읽는다):

  [{"id": "<경기 id>", "time": "YYYY-MM-DD HH:MM:SS", "room": "방 제목", "map": "맵 이름 또는 빈 값",
    "players": ["계정", ...], "results": {"계정": {"code": 1|2|3, "at": "..."}}}]

결과코드: 1 승 / 2 패 / 3 무. 원본 게임의 4(짧은 경기·무효)는 서버가 내보내지 않는다.
players = 게임 중계(TCP 4901)에 들어온 계정 → 혼자 만든 방은 사람 1명이라 사이트가 가져오지 않는다.
종족 정보는 서버가 알 수 없어 없다 (RACES 가 비어 있으면 화면에서 종족 칸·필터·통계를 숨긴다).
"""
from __future__ import annotations

KEY = "samchun"
TITLE = "삼국지천명2"
EXT = ".screplay"
HAS_REPLAYS = False          # 런처 업로드 미지원 — 경기 카드의 리플레이 버튼 대신 안내 문구
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
