"""사용자 설정 (게임별 설치 경로, 마지막 IP, 해상도 등) — 원자적 저장."""
import json
import os
import tempfile
import threading

from .paths import app_data_dir

_LOCK = threading.Lock()


class Settings:
    def __init__(self):
        self.path = app_data_dir() / "settings.json"
        self.data = {"selected": "rhakmu", "games": {}}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass
        if not isinstance(self.data.get("games"), dict):
            self.data["games"] = {}

    def game(self, game_id: str) -> dict:
        return self.data["games"].setdefault(game_id, {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        with _LOCK:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
