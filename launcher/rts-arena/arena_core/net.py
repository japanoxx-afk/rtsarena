"""HTTP 유틸: JSON 조회, 이어받기 다운로드, Synology NAS 공유 링크 해석."""
import hashlib
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RTS-Arena-Launcher"
CHUNK = 1024 * 512


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


def make_opener():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def no_cache(url: str) -> str:
    return url + ("&" if "?" in url else "?") + "t=" + str(time.time_ns())


def fetch_json(url: str, timeout=8):
    req = urllib.request.Request(no_cache(url), headers={"User-Agent": USER_AGENT,
                                                         "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


# ── Synology ───────────────────────────────────────────────────
_SHARE_RE = re.compile(r"^/(?:sharing|d/s)/([A-Za-z0-9_-]+)")


def is_synology_share(url: str) -> bool:
    return bool(_SHARE_RE.match(urllib.parse.urlparse(url).path))


def _json_string(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return raw


def _find_filename(text: str):
    m = re.search(r'"filename"\s*:\s*"((?:[^"\\]|\\.)+)"', text)
    return _json_string(m.group(1)) if m else None


def resolve_synology(url: str, opener) -> str:
    """Synology 파일 공유 링크(/sharing/<id>)를 실제 다운로드 주소로 바꾼다.

    1) 공유 페이지를 열어 세션 쿠키(sharing_sid)를 받는다.
    2) 페이지/세션 API에서 파일명을 찾는다.
    3) /fsdownload/<id>/<파일명> 으로 받는다 (같은 쿠키 필요).
    """
    parsed = urllib.parse.urlparse(url)
    share_id = _SHARE_RE.match(parsed.path).group(1)
    base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with opener.open(f"{base}/sharing/{share_id}", timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise DownloadError("NAS 공유 링크를 찾을 수 없습니다.\n"
                                "링크가 만료되었거나 공유가 해제되었는지 NAS 관리자에게 확인하세요.")
        raise DownloadError(f"NAS 공유 페이지 오류 (HTTP {e.code})")
    except urllib.error.URLError as e:
        raise DownloadError(f"NAS 서버에 연결할 수 없습니다: {e.reason}")

    if "password" in html.lower() and "sharing_sid" not in html and "filename" not in html:
        raise DownloadError("비밀번호가 걸린 공유 링크는 지원하지 않습니다.")

    filename = _find_filename(html)
    if not filename:
        # DSM 7: 세션 API가 JSON이 아니라 'SYNO.SDS.Session = {...}' 스크립트로 파일명을 준다
        api = (f"{base}/sharing/webapi/entry.cgi?api=SYNO.Core.Sharing.Session"
               f"&version=1&method=get&sharing_id=%22{share_id}%22")
        try:
            with opener.open(api, timeout=15) as resp:
                filename = _find_filename(resp.read().decode("utf-8", "replace"))
        except OSError:
            filename = None
    if filename:
        return f"{base}/fsdownload/{share_id}/{urllib.parse.quote(filename)}"
    return f"{base}/fsdownload/{share_id}/"


# ── 다운로드 ───────────────────────────────────────────────────
def download(url: str, dest: Path, progress=None, cancel=None,
             expected_size: int = 0, sha256: str = "", expect_zip=False) -> Path:
    """dest로 다운로드. .part 파일로 이어받기 지원, 크기/해시/ZIP 서명 검증."""
    opener = make_opener()
    if is_synology_share(url):
        if progress:
            progress(0, 0, "NAS 공유 링크 확인 중…")
        url = resolve_synology(url, opener)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Cache-Control": "no-cache"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:          # 이미 다 받은 상태
            part.unlink(missing_ok=True)
            return download(url, dest, progress, cancel, expected_size, sha256, expect_zip)
        raise DownloadError(f"다운로드 실패 (HTTP {e.code})")
    except urllib.error.URLError as e:
        raise DownloadError(f"서버에 연결할 수 없습니다: {e.reason}")

    with resp:
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" in ctype:
            raise DownloadError("파일 대신 웹페이지가 내려왔습니다. 공유 링크 설정(공개/만료)을 확인하세요.")
        if resp.status != 206:
            have = 0                          # 서버가 이어받기를 지원하지 않음
        length = int(resp.headers.get("Content-Length") or 0)
        total = have + length if length else expected_size
        done = have
        last = 0.0
        with open(part, "ab" if have else "wb") as out:
            while True:
                if cancel and cancel():
                    raise Cancelled()
                block = resp.read(CHUNK)
                if not block:
                    break
                out.write(block)
                done += len(block)
                now = time.monotonic()
                if progress and now - last > 0.15:
                    last = now
                    progress(done, total, None)
    if progress:
        progress(done, total, None)
    if total and done != total:
        raise DownloadError(f"다운로드가 중간에 끊겼습니다 ({done:,}/{total:,} bytes). 다시 시도하면 이어받습니다.")
    if expected_size and done != expected_size:
        part.unlink(missing_ok=True)
        raise DownloadError("파일 크기가 예상과 다릅니다.")
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(CHUNK * 4), b""):
                h.update(block)
        if h.hexdigest().lower() != sha256.lower():
            part.unlink(missing_ok=True)
            raise DownloadError("파일 무결성(SHA256) 검증에 실패했습니다.")
    with open(part, "rb") as f:
        head = f.read(4)
    if expect_zip and head[:2] != b"PK":
        part.unlink(missing_ok=True)
        raise DownloadError("받은 파일이 ZIP 형식이 아닙니다.")
    dest.unlink(missing_ok=True)
    part.replace(dest)
    return dest
