"""경로 규칙.

- 설정/모듈(게임별 런처) : %LOCALAPPDATA%\\RTS-Arena
- 기본 설치 위치         : C:\\Users\\<윈도우 사용자>\\Downloads
"""
import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(root) / "RTS-Arena"
    path.mkdir(parents=True, exist_ok=True)
    return path


def modules_dir(game_id: str) -> Path:
    path = app_data_dir() / "modules" / game_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_install_root() -> Path:
    """요구사항: 기본 압축 해제 위치는 C:\\Users\\<사용자>\\Downloads."""
    profile = os.environ.get("USERPROFILE")
    base = Path(profile) if profile else Path.home()
    return base / "Downloads"


def resource_dir() -> Path:
    """PyInstaller 번들 내부 리소스 위치 (assets 등)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def asset(name: str) -> Path:
    return resource_dir() / "assets" / name
