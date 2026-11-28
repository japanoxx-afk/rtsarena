"""게임 모듈 목록 — 상단 게임 바의 순서와 같다."""
from .base import ComingSoon, GameModule, NotReady
from .rhakmu import RhakMu


class TaejoWanggeon(ComingSoon):
    id = "wanggeon"
    name = "태조왕건"
    subtitle = "WANGGEON"
    icon = "icon_wanggeon.png"
    logo = "logo_wanggeon.png"


class Cheonmyeong2(ComingSoon):
    id = "cheonmyeong2"
    name = "삼국지 천명2"
    subtitle = "CHEONMYEONG II"
    icon = "icon_cheonmyeong2.png"
    logo = "logo_cheonmyeong2.png"


MODULES = [RhakMu, TaejoWanggeon, Cheonmyeong2]

__all__ = ["MODULES", "GameModule", "NotReady"]
