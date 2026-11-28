"""bridge/arena_bridge.py 테스트 (라크무 런처 없이 가짜 globals로 검사)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))
import arena_bridge  # noqa: E402


class FakeManager:
    applied = []

    def __init__(self, game_dir):
        self.available = True

    def read_settings(self):
        return dict(windowed=False, width=0, height=0, shader="Bicubic", maintas=True,
                    renderer="auto", confine=True)

    def apply_settings(self, windowed, width, height, **kw):
        FakeManager.applied.append((width, height))
        return True, ""


class BridgeTest(unittest.TestCase):
    def test_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "info.json"
            code = arena_bridge.main(["--arena-info", str(out)],
                                     {"APP_VERSION": "0.9116", "NOTES": "라크무 런처 — v0.9116\n• 테스트"})
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(data["version"], "0.9116")
        self.assertEqual(data["arena_api"], 1)
        self.assertIn("1280x720", data["features"]["resolutions"])

    def test_resolution_mapping(self):
        class App:
            base_dir = "."
            cfg = {"game_dir": "C:/Game"}

        bridge = arena_bridge.Bridge.__new__(arena_bridge.Bridge)
        bridge.app = App()
        bridge.g = {"save_config": lambda base, cfg: None, "WindowModeManager": FakeManager}
        bridge.apply_resolution("1920x1080")
        self.assertTrue(bridge.app.cfg["viewport_1280"])
        self.assertEqual(FakeManager.applied[-1], (1920, 1080))
        bridge.apply_resolution("1024x768")
        self.assertFalse(bridge.app.cfg["viewport_1280"])


if __name__ == "__main__":
    unittest.main()
