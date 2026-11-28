"""ZIP 설치본 압축 해제 (한글 파일명, 경로 탈출 방지, 용량 확인, 진행률)."""
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from .net import Cancelled


class InstallError(Exception):
    pass


def _member_name(info: zipfile.ZipInfo) -> str:
    """UTF-8 플래그가 없는 ZIP(윈도우 탐색기/반디집 등)은 CP949로 해석."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name.replace("\\", "/")


def plan(zip_path: Path, install_root: Path, fallback_folder: str):
    """압축을 풀 실제 폴더를 정한다.

    ZIP 안이 폴더 하나로 감싸져 있으면 install_root 바로 아래에 풀고,
    파일이 루트에 흩어져 있으면 install_root/fallback_folder 아래에 푼다.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = [_member_name(i) for i in zf.infolist()]
        total = sum(i.file_size for i in zf.infolist())
    tops = {PurePosixPath(n).parts[0] for n in names if PurePosixPath(n).parts}
    single_folder = len(tops) == 1 and any("/" in n.strip("/") for n in names)
    target = install_root if single_folder else install_root / fallback_folder
    top = next(iter(tops)) if single_folder else None
    return target, top, total


def extract(zip_path: Path, target: Path, progress=None, cancel=None):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos) or 1
        free = shutil.disk_usage(root).free
        if free < total + 50 * 1024 * 1024:
            raise InstallError(f"디스크 공간이 부족합니다. 필요: {total / 2**30:.2f} GB, "
                               f"남은 공간: {free / 2**30:.2f} GB")
        done = 0
        for info in infos:
            if cancel and cancel():
                raise Cancelled()
            name = _member_name(info)
            dest = (root / name).resolve()
            if root != dest and root not in dest.parents:
                raise InstallError(f"안전하지 않은 경로가 포함된 압축 파일입니다: {name}")
            if name.endswith("/"):
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                while True:
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
            if cancel and cancel():
                raise Cancelled()
    return root


def find_dir_with(root: Path, required: list, max_depth=4):
    """required 파일들이 모두 있는 첫 폴더를 찾는다 (얕은 폴더 우선)."""
    root = Path(root)
    queue = [(root, 0)]
    while queue:
        folder, depth = queue.pop(0)
        try:
            if all((folder / r).is_file() for r in required):
                return folder
            if depth < max_depth:
                queue.extend((p, depth + 1) for p in sorted(folder.iterdir()) if p.is_dir())
        except OSError:
            continue
    return None
