"""게임 모듈 인터페이스.

새 RTS 게임을 추가할 때는 GameModule을 상속한 파일 하나를 만들고
arena_games/__init__.py 의 MODULES 목록에 넣으면 상단 게임 바에 나타난다.
"""
import os
import subprocess
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class NotReady(Exception):
    """기능이 아직 배포되지 않았을 때 (사용자에게 안내만 한다)."""


class GameModule:
    id = "base"
    name = "게임"
    subtitle = ""
    icon = "icon_default.png"
    logo = "logo_default.png"
    available = True                  # False면 상단 바에 '준비 중'으로만 표시
    required_files: list = []         # 설치 폴더 판별용
    fallback_folder = "Game"          # ZIP이 폴더로 감싸져 있지 않을 때 만들 폴더명
    known_install_dirs: list = []     # '이미 설치됨' 선택 시 미리 채워 줄 경로
    play_actions = [("play", "게임 시작")]
    resolutions = []                  # [(key, label)]
    default_resolution = None

    def __init__(self, settings, catalog_provider):
        self.settings = settings
        self._catalog = catalog_provider

    # ── 상태 ─────────────────────────────────────────────
    @property
    def state(self) -> dict:
        return self.settings.game(self.id)

    @property
    def catalog(self) -> dict:
        return (self._catalog().get("games") or {}).get(self.id, {})

    def save(self):
        self.settings.save()

    # ── 설치 경로 ─────────────────────────────────────────
    def validate(self, path) -> Path | None:
        """폴더 또는 그 안의 실행 파일 경로를 받아 유효한 게임 폴더를 돌려준다."""
        if not path:
            return None
        p = Path(str(path).strip().strip('"')).expanduser()
        if p.is_file():
            p = p.parent
        if p.is_dir() and all((p / f).is_file() for f in self.required_files):
            return p.resolve()
        return None

    @property
    def game_dir(self) -> Path | None:
        return self.validate(self.state.get("game_dir"))

    @property
    def is_installed(self) -> bool:
        return self.game_dir is not None

    def set_game_dir(self, path) -> Path:
        valid = self.validate(path)
        if not valid:
            raise ValueError("선택한 폴더에 " + ", ".join(self.required_files) + " 파일이 없습니다.")
        self.state["game_dir"] = str(valid)
        self.save()
        return valid

    def guess_existing_dir(self) -> str:
        for d in [self.state.get("game_dir"), *self.known_install_dirs]:
            if d and self.validate(d):
                return str(self.validate(d))
        return ""

    # ── 런처 모듈 / 업데이트 내역 ─────────────────────────
    def module_version(self) -> str:
        return self.state.get("module_version", "")

    def check_module_update(self, progress=None) -> str:
        return "업데이트 대상이 없습니다."

    def notes(self) -> str:
        return ""

    # ── 실행 ─────────────────────────────────────────────
    def launch(self, action: str, resolution: str | None, ip: str | None = None):
        raise NotReady("아직 지원하지 않습니다.")

    def open_replay(self):
        raise NotReady("리플레이 기능은 준비 중입니다.")

    def open_map_editor(self, progress=None):
        raise NotReady("맵 에디터는 준비 중입니다.")

    @staticmethod
    def _popen(args, cwd):
        return subprocess.Popen([str(a) for a in args], cwd=str(cwd),
                                env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})


class ComingSoon(GameModule):
    available = False

    def notes(self):
        return f"{self.name} 모듈은 준비 중입니다.\n\n기존 {self.name} 런처를 통합런처 모듈로 옮기는 작업이 끝나면 이곳에서 바로 설치·실행할 수 있습니다."
