"""카탈로그(게임 목록·주소) 조회와 통합런처 자체 업데이트."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import ARENA_VERSION, CATALOG_URL
from .net import download, fetch_json
from .paths import app_data_dir

# 네트워크가 안 될 때 사용하는 내장 기본값. 원격 arena.json이 있으면 덮어쓴다.
DEFAULT_CATALOG = {
    "arena_version": ARENA_VERSION,
    "games": {
        "rhakmu": {
            "version_url": "https://raw.githubusercontent.com/japanoxx-afk/new-rakum/main/version.json",
            "setup_url": "https://akansas.synology.me:5001/sharing/TQcoAipwf",
        },
    },
}


def version_key(value) -> tuple:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", str(value or "")):
        return (0,)
    return tuple(int(p) for p in str(value).split("."))


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_catalog(online=True):
    """원격 → 캐시 → 내장 기본값 순서로 카탈로그를 만든다. (catalog, online여부)"""
    cache = app_data_dir() / "catalog_cache.json"
    if online:
        try:
            remote = fetch_json(CATALOG_URL)
            if isinstance(remote, dict):
                cache.write_text(json.dumps(remote, ensure_ascii=False, indent=2), encoding="utf-8")
                return _merge(DEFAULT_CATALOG, remote), True
        except Exception:
            pass
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        return _merge(DEFAULT_CATALOG, cached), False
    except (OSError, ValueError):
        return DEFAULT_CATALOG, False


# ── 통합런처 자체 업데이트 ─────────────────────────────────────
def cleanup_old_binaries():
    if getattr(sys, "frozen", False):
        for old in Path(sys.executable).parent.glob("*.old"):
            try:
                old.unlink()
            except OSError:
                pass


def arena_update_available(catalog):
    latest = catalog.get("arena_version", ARENA_VERSION)
    if catalog.get("arena_url") and version_key(latest) > version_key(ARENA_VERSION):
        return latest
    return None


def apply_arena_update(catalog, progress=None):
    """새 exe를 받아 현재 exe와 교체한 뒤 재실행한다 (실행 중 exe는 rename 가능)."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("개발 모드(python 실행)에서는 자체 업데이트를 하지 않습니다.")
    current = Path(sys.executable)
    new = current.with_name(current.stem + ".new.exe")
    download(catalog["arena_url"], new, progress,
             expected_size=int(catalog.get("arena_size") or 0),
             sha256=catalog.get("arena_sha256", ""))
    if new.read_bytes()[:2] != b"MZ":
        new.unlink(missing_ok=True)
        raise RuntimeError("받은 파일이 실행 파일이 아닙니다.")
    old = current.with_name(current.name + ".old")
    old.unlink(missing_ok=True)
    os.replace(current, old)
    try:
        os.replace(new, current)
    except OSError:
        os.replace(old, current)
        raise
    subprocess.Popen([str(current)], cwd=str(current.parent),
                     env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
