"""최신 런처 정보 — new-rakum 저장소 main 의 version.json (런처 자동 업데이트가 보는 것과 같은 파일).

사이트는 이 파일을 주기적으로 다시 읽어 "최신 런처 받기" 버튼을 항상 최신 버전으로 맞춘다.
- 페이지 요청은 절대 기다리지 않는다: 캐시를 바로 돌려주고, 오래됐으면 뒤에서 새로 받는다.
- 받기 실패 시 마지막으로 받은 값(data/launcher_latest.json)을 계속 쓴다 (재시작해도 유지).
- 다운로드 주소는 허용된 GitHub 경로(new-rakum 저장소)만 받는다. version.json 이 이상하면 무시.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger("arena.launcher")

ALLOWED_PREFIXES = (
    "https://raw.githubusercontent.com/japanoxx-afk/new-rakum/",
    "https://github.com/japanoxx-afk/new-rakum/",
)
REFRESH_SECONDS = 600     # 10분마다 확인 (GitHub raw 는 몇 분 캐시가 있다)
RETRY_SECONDS = 60        # 실패하면 1분 뒤 다시


def validate(obj) -> dict | None:
    """version.json 내용 검사. 문제가 있으면 None."""
    if not isinstance(obj, dict):
        return None
    ver, url = obj.get("launcher_version"), obj.get("launcher_url")
    sha, size = obj.get("launcher_sha256"), obj.get("launcher_size")
    if not (isinstance(ver, str) and re.fullmatch(r"[0-9A-Za-z._-]{1,20}", ver)):
        return None
    if not (isinstance(url, str) and url.startswith(ALLOWED_PREFIXES) and len(url) < 300
            and re.fullmatch(r"[A-Za-z0-9:/._%-]+", url) and ".." not in url):
        return None
    out = {"version": ver, "url": url}
    if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        out["sha256"] = sha.lower()
    if isinstance(size, int) and 0 < size < 2_000_000_000:
        out["size"] = size
    return out


def _http_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "RtsArena", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read(64 * 1024)


class LauncherInfo:
    def __init__(self, version_url: str, cache_file: Path,
                 fetch: Callable[[str], bytes] = _http_fetch, clock: Callable[[], float] = time.time):
        self.version_url = version_url
        self.cache_file = Path(cache_file)
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._busy = False
        self._info: dict | None = None
        self._next = 0.0          # 다음 확인 시각
        self.last_error = ""
        try:
            self._info = validate(json.loads(self.cache_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def refresh(self) -> bool:
        """지금 바로 받아 본다 (뒤 스레드나 테스트에서 호출). 성공하면 True."""
        if not self.version_url:
            return False
        try:
            info = validate(json.loads(self._fetch(self.version_url).decode("utf-8")))
            if not info:
                raise ValueError("version.json 형식이 맞지 않음")
        except Exception as e:  # noqa: BLE001 — 네트워크·형식 오류 모두 기존 값 유지
            with self._lock:
                self.last_error = str(e)[:200]
                self._next = self._clock() + RETRY_SECONDS
            log.warning("최신 런처 정보 받기 실패: %s", e)
            return False
        with self._lock:
            changed = info != self._info
            self._info, self.last_error = info, ""
            self._next = self._clock() + REFRESH_SECONDS
        if changed:
            log.info("최신 런처: v%s", info["version"])
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.cache_file.with_suffix(".tmp")
                raw = {"launcher_version": info["version"], "launcher_url": info["url"],
                       "launcher_sha256": info.get("sha256"), "launcher_size": info.get("size")}
                tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")   # version.json 과 같은 모양
                tmp.replace(self.cache_file)
            except OSError as e:
                log.warning("런처 정보 캐시 저장 실패: %s", e)
        return True

    def _run(self):
        try:
            self.refresh()
        finally:
            with self._lock:
                self._busy = False

    def get(self) -> dict | None:
        """캐시를 바로 돌려준다. 오래됐으면 뒤에서 새로 받기 시작."""
        with self._lock:
            info = self._info
            start = bool(self.version_url) and not self._busy and self._clock() >= self._next
            if start:
                self._busy = True
        if start:
            threading.Thread(target=self._run, daemon=True, name="launcher-info").start()
        return info
