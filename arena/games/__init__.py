"""게임(타이틀) 목록. 상단 드롭박스가 이 순서대로 보여 준다.

새 RTS 를 붙일 때: 이 폴더에 <key>.py (rakmu.py 와 같은 함수들) 를 만들고 GAMES 에 등록, ready=True.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from . import rakmu, samchun


@dataclass(frozen=True)
class Game:
    key: str
    title: str
    ready: bool
    module: ModuleType | None = None


GAMES = [
    Game("rakmu", rakmu.TITLE, True, rakmu),
    Game("samchun", samchun.TITLE, True, samchun),   # 호환 서버 matches.json 만 (리플레이 업로드 없음)
    Game("iop", "임팩트 오브 파워", False),      # 준비 중 (리플레이 형식 조사 후 추가)
]
BY_KEY = {g.key: g for g in GAMES}
DEFAULT = "rakmu"


def get(key: str) -> Game | None:
    return BY_KEY.get(key)
