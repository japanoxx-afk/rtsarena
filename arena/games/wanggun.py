"""태조왕건 — 더미서버(japanoxx-afk/wanggun dummyserver.py 2.0+) 전적.

경기·승패는 더미서버가 쓰는 data/matches.json 에서 온다
(라크무 로비 서버·삼국지천명2 호환 서버와 같은 형식 — arena/rakmu_server.py ServerRecord 로 그대로 읽는다):

  [{"id": "<경기 id>", "time": "YYYY-MM-DD HH:MM:SS"(첫 결과 시각), "started": "방 생성 시각",
    "room": "방 제목", "map": "방 정보의 맵 이름 또는 빈 값",
    "players": ["계정", ...], "results": {"계정": {"code": 1|2|3, "at": "...", "dis"?: true}}}]

결과코드: 1 승 / 2 패 / 3 무. Dis(접속 종료 등)는 게임 안 전적처럼 패(2)로 내보내고 "dis": true 가 붙는다.
players = 결과를 처음 보고할 때 그 방에 있던 계정 → 혼자 만든 방은 사람 1명이라 사이트가 가져오지 않는다
(서버 2.0 도 사람 2명 이상 경기만 게임 안 순위에 넣는다).
종족(왕건/견훤/궁예)은 서버가 알 수 없어 없다 (RACES 가 비어 있으면 화면에서 종족 칸·필터·통계를 숨긴다).
리플레이 업로드는 아직 없다.
"""
from __future__ import annotations

KEY = "wanggun"
TITLE = "태조왕건"
EXT = ""
HAS_REPLAYS = False
RULE_NOTE = "태조왕건 게임 안 순위와 같은 규칙 (Dis 는 패)"

RACES: tuple = ()
RACE_COLORS: tuple = ()
RACE_LETTERS: tuple = ()
UNKNOWN_MAP = "맵 정보 없음"
RELEASES_URL = "https://github.com/japanoxx-afk/wanggun/releases"


def race_name(race) -> str:
    return "-"


def map_display(path: str) -> str:
    """서버가 방 정보에서 읽은 맵 파일 이름(확장자 없음). 못 읽었으면 빈 값 → '맵 정보 없음'."""
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
