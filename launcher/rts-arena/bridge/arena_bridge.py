"""RTS-Arena 통합런처 ↔ 라크무 런처 연결부 (라크무 런처 저장소에 추가하는 파일).

통합런처는 라크무 런처 exe를 아래 인자로 '숨김 모드' 실행한다. 라크무 런처의 기존
기능(패치·hosts·해상도·리플레이 기록·전적 업로드·마우스 가두기)을 그대로 쓰되
라크무 런처 창은 띄우지 않는다.

  --arena-info <out.json>
        버전/업데이트 내역/지원 기능을 JSON으로 쓰고 바로 끝낸다 (창 없음).
  --arena-launch single|multi --game-dir <폴더> [--resolution 1280x720]
        게임을 실행하고, 게임이 끝날 때까지 백그라운드 기능을 유지한 뒤 종료한다.
        multi 는 라크무 런처에 저장된 서버 IP(기본: 공식 프리서버)를 사용한다.
  --arena-replay --game-dir <폴더>
        리플레이 목록 창만 띄운다 (라크무 런처의 리플레이 탭과 동일).
  --arena-editor --game-dir <폴더>
        맵 에디터(브라우저)를 열고, 작은 상태 창을 닫으면 에디터 서버를 끈다.

launcher.py 의 `if __name__ == "__main__":` 바로 아래에 다음 3줄을 넣는다:

    if any(a.startswith('--arena-') for a in sys.argv[1:]):
        import arena_bridge
        sys.exit(arena_bridge.main(sys.argv[1:], globals()))

그리고 version.json 에 "arena_api": 1 을 추가해 배포하면 통합런처가 연동을 시작한다.
"""
import json
import pathlib
import time

ARENA_API = 1

# 통합런처 해상도 선택 → (내부 해상도 1280×720 사용, cnc-ddraw 출력 크기)
RESOLUTIONS = {
    "1024x768": (False, 0, 0),
    "1280x720": (True, 0, 0),
    "1920x1080": (True, 1920, 1080),
    "2560x1440": (True, 2560, 1440),
}

START_TIMEOUT = 90      # 게임 프로세스가 뜰 때까지 기다리는 최대 시간(초)
AFTER_GAME_GRACE = 15   # 게임 종료 후 리플레이 저장·전적 업로드를 마칠 여유(초)


def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def write_info(path, g):
    data = {
        "version": g["APP_VERSION"],
        "arena_api": ARENA_API,
        "notes": g["NOTES"],
        "features": {"single": True, "multi": True, "replay": True, "map_editor": True,
                     "resolutions": list(RESOLUTIONS)},
    }
    pathlib.Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return 0


class Bridge:
    def __init__(self, argv, g):
        self.argv, self.g = argv, g
        self.tk, self.ttk, self.mb = g["tk"], g["ttk"], g["messagebox"]
        self.errors = []
        # 실패 메시지를 기억해 두었다가 숨김 런처를 정리한다 (메시지 창은 그대로 보인다)
        original = self.mb.showerror

        def showerror(*a, **k):
            self.errors.append(a[1] if len(a) > 1 else k.get("message", ""))
            return original(*a, **k)
        self.mb.showerror = showerror

        self.app = g["App"]()
        self.app.withdraw()
        game_dir = _arg(argv, "--game-dir")
        if game_dir:
            self.app.gamedir_var.set(game_dir)
            self.app.cfg["game_dir"] = game_dir
            self.app.winmode.game_dir = game_dir

    # ── 정리 ────────────────────────────────────────────
    def shutdown(self):
        app = self.app
        for name in ("map_editor", "cursor_guard", "recorder", "arena"):
            try:
                getattr(app, name).stop()
            except Exception:
                pass
        try:
            if app.server.running:
                app.server.stop()
        except Exception:
            pass
        try:
            app.destroy()
        except Exception:
            pass

    # ── 게임 실행 ───────────────────────────────────────
    def apply_resolution(self, key):
        if key not in RESOLUTIONS:
            return
        highres, width, height = RESOLUTIONS[key]
        app = self.app
        app.cfg["viewport_1280"] = highres
        try:
            self.g["save_config"](app.base_dir, app.cfg)
        except OSError:
            pass
        try:
            manager = self.g["WindowModeManager"](app.cfg.get("game_dir"))
            if not manager.available:
                return                      # ddraw.ini 가 없으면 _highres_ready 가 설치한다
            s = manager.read_settings()
            if (s["width"], s["height"]) != (width, height):
                manager.apply_settings(s["windowed"], width, height, shader=s.get("shader", "Bicubic"),
                                       maintas=s.get("maintas", True), renderer=s.get("renderer", "auto"),
                                       confine=s.get("confine", True))
        except Exception:
            pass                            # 출력 크기는 부가 기능: 실패해도 게임은 실행

    def launch(self, mode):
        app, g = self.app, self.g
        self.apply_resolution(_arg(self.argv, "--resolution", "1280x720"))
        if mode == "multi":
            if not app.saved_ip_var.get().strip():
                app.saved_ip_var.set(app.cfg.get("last_server_ip") or g["DEFAULT_SERVER_IP"])
            app.after(50, app._on_multi_play)
        else:
            app.after(50, app._on_single_play)

        find = g["replay_memory"].find_game_pid
        state = {"seen": False, "deadline": time.monotonic() + START_TIMEOUT, "ended": None}

        def watch():
            running = bool(find())
            now = time.monotonic()
            if running:
                state["seen"] = True
            elif state["seen"]:
                state["ended"] = state["ended"] or now
                if now - state["ended"] >= AFTER_GAME_GRACE:
                    return self.shutdown()
            elif self.errors or now > state["deadline"]:
                return self.shutdown()      # 실행 전에 실패했거나 게임이 뜨지 않음
            app.after(1000, watch)
        app.after(2000, watch)
        app.mainloop()
        return 0 if state["seen"] else 1

    # ── 리플레이 / 맵 에디터 창 ─────────────────────────
    def _window(self, title, size):
        win = self.tk.Toplevel(self.app)
        win.title(title)
        win.geometry(size)
        win.protocol("WM_DELETE_WINDOW", self.shutdown)
        return win

    def replay(self):
        win = self._window("라크무 리플레이", "980x640")
        nb = self.ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.app._build_replay_tab(nb)      # 라크무 런처의 리플레이 탭을 그대로 사용
        self.app.mainloop()
        return 0

    def editor(self):
        app, tk, ttk = self.app, self.tk, self.ttk
        win = self._window("라크무 맵 에디터", "420x170")
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="맵 에디터가 브라우저에서 열렸습니다.\n이 창을 닫으면 맵 에디터가 종료됩니다.",
                  justify="left").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="브라우저로 다시 열기", command=app._on_open_map_editor).pack(side="left")
        ttk.Button(row, text="맵 폴더 열기", command=app._on_open_maps_folder).pack(side="left", padx=6)
        ttk.Button(row, text="닫기", command=self.shutdown).pack(side="right")
        app.after(100, app._on_open_map_editor)
        app.mainloop()
        return 0


def main(argv, g):
    out = _arg(argv, "--arena-info")
    if out:
        return write_info(out, g)
    if not g["is_admin"]():
        # run_as_admin()은 인자 없이 재실행하므로 쓰지 않는다. 통합런처가 관리자 권한으로 실행해야 한다.
        root = g["tk"].Tk()
        root.withdraw()
        g["messagebox"].showerror("라크무", "관리자 권한이 필요합니다.\nRTS-Arena 런처를 관리자 권한으로 실행해 주세요.")
        root.destroy()
        return 3
    bridge = Bridge(argv, g)
    if "--arena-launch" in argv:
        return bridge.launch(_arg(argv, "--arena-launch", "single"))
    if "--arena-replay" in argv:
        return bridge.replay()
    if "--arena-editor" in argv:
        return bridge.editor()
    bridge.shutdown()
    return 2
