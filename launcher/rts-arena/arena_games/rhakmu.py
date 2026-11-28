"""라크무 모듈.

통합런처는 '껍데기'만 담당하고, 실제 게임 준비(패치·hosts·로컬 서버·해상도 적용)는
기존 라크무 런처(RhakMuLauncher_vX.exe)를 숨김 모드(--arena-launch)로 호출해 처리한다.
그래서 라크무 런처가 GitHub에 새 버전을 올리면 통합런처도 그 exe를 받아 그대로 쓴다.

라크무 런처에 필요한 숨김 모드 인터페이스 (bridge/arena_bridge.py):
  --arena-info <out.json>                       버전·업데이트 내역·지원 기능
  --arena-launch single|multi --game-dir D --resolution R
        (멀티는 라크무 런처에 저장된 서버 IP — 기본값 공식 프리서버 — 를 그대로 사용)
  --arena-replay --game-dir D
  --arena-editor --game-dir D

연동 여부는 라크무 version.json 의 "arena_api" 값(1 이상)으로 판단한다. 이 값이 없는
구버전 라크무 런처는 절대 숨김 인자로 실행하지 않는다 (일반 런처 창이 떠 버리므로).
"""
import json
import subprocess
import tempfile
from pathlib import Path

from arena_core.net import download, fetch_json
from arena_core.paths import modules_dir
from arena_core.updater import version_key

from .base import NO_WINDOW, GameModule, NotReady

EXE_PREFIX = "RhakMuLauncher_v"


class RhakMu(GameModule):
    id = "rhakmu"
    name = "라크무"
    subtitle = "RHAKMU"
    icon = "icon_rhakmu.png"
    logo = "logo_rhakmu.png"
    required_files = ["Launcher.exe", "Rhakmu.exe"]
    fallback_folder = "RhakMu"
    known_install_dirs = [r"C:\Program Files (x86)\TriggerSoft\RhakMu"]
    play_actions = [("multi", "멀티플레이"), ("single", "싱글플레이")]
    resolutions = [
        ("1024x768", "1024×768 (원본)"),
        ("1280x720", "1280×720 (와이드)"),
        ("1920x1080", "1920×1080 (와이드 확대)"),
        ("2560x1440", "2560×1440 (와이드 확대)"),
    ]
    default_resolution = "1280x720"

    # ── 라크무 런처 exe 관리 ─────────────────────────────
    @property
    def module_exe(self) -> Path | None:
        p = self.state.get("module_exe")
        return Path(p) if p and Path(p).is_file() else None

    def arena_supported(self) -> bool:
        return bool(self.module_exe) and int(self.state.get("arena_api") or 0) >= 1

    def check_module_update(self, progress=None) -> str:
        """라크무 런처 최신 버전 확인 → 필요하면 받아서 교체 → 업데이트 내역 갱신."""
        info = fetch_json(self.catalog["version_url"])
        latest = str(info.get("launcher_version", ""))
        url = info.get("launcher_url", "")
        if not latest or not url:
            raise RuntimeError("라크무 런처 버전 정보가 올바르지 않습니다.")
        current = self.module_version()
        updated = False
        if not self.module_exe or version_key(latest) > version_key(current):
            folder = modules_dir(self.id)
            target = folder / f"{EXE_PREFIX}{latest}.exe"
            if progress:
                progress(0, 0, f"라크무 런처 v{latest} 받는 중…")
            download(url, target, progress,
                     expected_size=int(info.get("launcher_size") or 0),
                     sha256=info.get("launcher_sha256", ""))
            if target.read_bytes()[:2] != b"MZ":
                target.unlink(missing_ok=True)
                raise RuntimeError("받은 라크무 런처가 실행 파일이 아닙니다.")
            self.state.update(module_exe=str(target), module_version=latest)
            self.state.pop("arena_info", None)
            for old in folder.glob(f"{EXE_PREFIX}*.exe"):
                if old != target:
                    try:
                        old.unlink()
                    except OSError:
                        pass                    # 실행 중이면 다음 기회에 정리
            updated = True
        if version_key(latest) >= version_key(self.module_version()):
            self.state["arena_api"] = int(info.get("arena_api") or 0)
        self.save()
        if self.arena_supported() and (updated or not self.state.get("arena_info")):
            self._refresh_arena_info()
        if updated:
            return f"라크무 런처를 v{latest}로 업데이트했습니다."
        return f"라크무 런처가 최신 버전입니다 (v{latest})."

    def _refresh_arena_info(self):
        exe = self.module_exe
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "info.json"
            subprocess.run([str(exe), "--arena-info", str(out)], cwd=str(exe.parent),
                           timeout=90, creationflags=NO_WINDOW)
            data = json.loads(out.read_text(encoding="utf-8"))
        self.state["arena_info"] = data
        self.save()

    @property
    def features(self) -> dict:
        return (self.state.get("arena_info") or {}).get("features", {})

    def notes(self) -> str:
        info = self.state.get("arena_info") or {}
        if info.get("notes"):
            return info["notes"]
        if self.module_exe and not self.arena_supported():
            return (f"받아 둔 라크무 런처 v{self.module_version()}는 아직 통합런처 연동(arena_api)을 지원하지 않습니다.\n\n"
                    "연동 기능이 들어간 라크무 런처가 배포되면 자동으로 업데이트되며,\n"
                    "그때부터 이곳에 업데이트 내역이 표시되고 게임을 바로 실행할 수 있습니다.")
        return "라크무 런처 정보를 불러오는 중입니다…\n\n인터넷 연결을 확인한 뒤 '런처 업데이트 확인'을 눌러 주세요."

    # ── 실행 ─────────────────────────────────────────────
    def _require_bridge(self):
        if not self.module_exe:
            raise NotReady("라크무 런처를 아직 받지 못했습니다.\n'런처 업데이트 확인'을 눌러 주세요.")
        if not self.arena_supported():
            raise NotReady("통합런처 연동이 들어간 라크무 런처가 아직 배포되지 않았습니다.\n"
                           f"현재 라크무 런처: v{self.module_version()}")
        return self.module_exe

    def launch(self, action, resolution, ip=None):
        exe = self._require_bridge()
        return self._popen([exe, "--arena-launch", action, "--game-dir", self.game_dir,
                            "--resolution", resolution or self.default_resolution], exe.parent)

    def open_replay(self):
        exe = self._require_bridge()
        return self._popen([exe, "--arena-replay", "--game-dir", self.game_dir], exe.parent)

    def open_map_editor(self, progress=None):
        exe = self._require_bridge()
        return self._popen([exe, "--arena-editor", "--game-dir", self.game_dir], exe.parent)
