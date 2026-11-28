"""RTS-Arena Launcher — 여러 RTS 게임 런처를 하나로 묶은 통합런처.

구조
  arena.py          화면 (배틀넷 스타일: 상단 게임 바 → 게임별 실행 화면)
  arena_ui.py       다크 테마 위젯/다이얼로그
  arena_core/       설정·다운로드(NAS)·압축 해제·카탈로그/자체 업데이트
  arena_games/      게임 모듈 (라크무, 준비 중인 게임들)
"""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from arena_core import ARENA_NAME, ARENA_VERSION, DISCORD_URL, YOUTUBE_URL
from arena_core import updater
from arena_core.installer import extract, find_dir_with, plan
from arena_core.net import Cancelled, download
from arena_core.paths import app_data_dir, asset, default_install_root
from arena_core.settings import Settings
from arena_games import MODULES, NotReady
from arena_ui import (C, FlatButton, Tooltip, alert, ask_folder, ask_text, choose,
                      confirm, font)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True                      # 윈도우가 아니면 검사하지 않음


def relaunch_as_admin():
    """hosts 수정·게임 패치에 관리자 권한이 필요 (exe는 매니페스트로 자동 요청)."""
    if os.name != "nt" or is_admin() or getattr(sys, "frozen", False):
        return
    params = " ".join(f'"{a}"' for a in [os.path.abspath(__file__), *sys.argv[1:]])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)


def open_folder(path):
    if os.name == "nt":
        os.startfile(str(path))          # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ArenaApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{ARENA_NAME}  v{ARENA_VERSION}")
        self.configure(bg=C["bg"])
        # 고DPI 모니터에서 창 크기 보정 (글꼴은 tk가 자동 보정)
        self.k = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.geometry(f"{self.px(1180)}x{self.px(740)}")
        self.minsize(self.px(1040), self.px(660))
        try:
            self.iconbitmap(str(asset("app.ico")))
        except tk.TclError:
            pass

        self.settings = Settings()
        self.catalog, _ = updater.load_catalog(online=False)
        self.modules = [cls(self.settings, lambda: self.catalog) for cls in MODULES]
        self.images = {}
        self.events = queue.Queue()
        self.busy = False
        self.cancel_flag = False
        self.running = {}                 # game_id -> Popen (게임 실행 중)

        self._style()
        self._build_shell()
        sel = self.settings.get("selected", "rhakmu")
        self.select(next((m for m in self.modules if m.id == sel and m.available), self.modules[0]))
        self.after(100, self._pump)
        self.after(300, self._startup_checks)

    # ── 기본 골격 ─────────────────────────────────────────
    def px(self, v):
        return int(v * self.k)

    def img(self, name):
        if name not in self.images:
            try:
                self.images[name] = tk.PhotoImage(file=str(asset(name)))
            except tk.TclError:
                self.images[name] = None
        return self.images[name]

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Arena.Horizontal.TProgressbar", troughcolor=C["card"], background=C["blue"],
                    bordercolor=C["card"], lightcolor=C["blue"], darkcolor=C["blue"], thickness=6)
        s.configure("Arena.Vertical.TScrollbar", troughcolor=C["card"], background=C["card_hi"],
                    bordercolor=C["card"], arrowcolor=C["muted"], lightcolor=C["card_hi"],
                    darkcolor=C["card_hi"], gripcount=0)
        s.layout("Arena.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        s.map("Arena.Vertical.TScrollbar", background=[("active", C["line"])])

    def _build_shell(self):
        # 상단 바: 로고 / 커뮤니티 아이콘
        top = tk.Frame(self, bg=C["top"], height=self.px(58))
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, image=self.img("arena_logo.png"), bg=C["top"]).pack(side="left", padx=(20, 8))
        tk.Label(top, text=f"v{ARENA_VERSION}", bg=C["top"], fg=C["dim"], font=font(9)).pack(side="left")
        for icon, url, tip in (("youtube.png", YOUTUBE_URL, "유튜브 채널"),
                               ("discord.png", DISCORD_URL, "디스코드 커뮤니티")):
            b = FlatButton(top, image=self.img(icon), variant="ghost", padx=10, pady=8,
                           command=lambda u=url: webbrowser.open(u))
            b.pack(side="right", padx=(0, 6) if icon == "youtube.png" else (0, 2))
            Tooltip(b, tip)

        # 게임 바 (배틀넷 즐겨찾기 줄)
        strip = tk.Frame(self, bg=C["strip"], height=self.px(84))
        strip.pack(fill="x", padx=10, pady=(8, 0))
        strip.pack_propagate(False)
        tk.Label(strip, text="게임", bg=C["strip"], fg=C["muted"], font=font(10, "bold")).pack(side="left", padx=(20, 14))
        self.tiles = {}
        for m in self.modules:
            self.tiles[m.id] = self._make_tile(strip, m)

        # 본문
        self.body = tk.Frame(self, bg=C["bg"])
        self.body.pack(fill="both", expand=True, padx=10, pady=10)

        # 하단 상태 바
        bar = tk.Frame(self, bg=C["top"], height=self.px(34))
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status = tk.StringVar(value="준비 완료")
        tk.Label(bar, textvariable=self.status, bg=C["top"], fg=C["muted"], font=font(9),
                 anchor="w").pack(side="left", padx=16, fill="x", expand=True)
        self.cancel_btn = FlatButton(bar, text="취소", variant="ghost", size=9, pady=3,
                                     command=self._request_cancel)
        self.pct = tk.Label(bar, text="", bg=C["top"], fg=C["text"], font=font(9, "bold"), width=6)
        self.progress = ttk.Progressbar(bar, style="Arena.Horizontal.TProgressbar", length=280,
                                        mode="determinate", maximum=1000)

    def _make_tile(self, parent, module):
        wrap = tk.Frame(parent, bg=C["strip"])
        wrap.pack(side="left", fill="y", padx=2)
        tile = tk.Label(wrap, image=self.img(module.icon), bg=C["strip"], cursor="hand2",
                        padx=14, pady=10)
        tile.pack(fill="both", expand=True)
        line = tk.Frame(wrap, height=3, bg=C["strip"])
        line.pack(fill="x", side="bottom")
        tip = module.name if module.available else f"{module.name} (준비 중)"
        Tooltip(tile, tip)
        tile.bind("<Enter>", lambda e: tile.config(bg=C["card"]))
        tile.bind("<Leave>", lambda e: tile.config(bg=C["strip"]))
        tile.bind("<ButtonRelease-1>", lambda e: self.select(module))
        return line

    # ── 게임 선택 / 화면 구성 ─────────────────────────────
    def select(self, module):
        self.current = module
        if module.available:
            self.settings.set("selected", module.id)
        for gid, line in self.tiles.items():
            line.config(bg=C["blue"] if gid == module.id else C["strip"])
        self.render()

    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        m = self.current
        left = tk.Frame(self.body, bg=C["panel"], width=self.px(380))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(self.body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self._render_left(left, m)
        self._render_right(right, m)

    def _render_left(self, left, m):
        tk.Label(left, image=self.img(m.logo), bg=C["panel"]).pack(pady=(26, 0))
        tk.Frame(left, bg=C["panel"]).pack(fill="both", expand=True)
        box = tk.Frame(left, bg=C["panel"], padx=22)
        box.pack(fill="x", pady=(0, 18))

        if not m.available:
            b = FlatButton(box, text="준비 중", variant="primary", size=16, pady=14)
            b.pack(fill="x")
            b.set_enabled(False)
            tk.Label(box, text="이 게임은 곧 통합런처에 추가됩니다.", bg=C["panel"], fg=C["muted"],
                     font=font(9)).pack(anchor="w", pady=(8, 0))
            return

        running = m.id in self.running
        if not m.is_installed:
            row = tk.Frame(box, bg=C["panel"])
            row.pack(fill="x")
            self.main_btn = FlatButton(row, text="설치", variant="primary", size=16, pady=14,
                                       command=self.on_install)
            self.main_btn.pack(side="left", fill="x", expand=True)
            self._gear(row, m)
            self.main_btn.set_enabled(not self.busy)
            tk.Label(box, text="게임이 설치되지 않았습니다.", bg=C["panel"], fg=C["muted"],
                     font=font(9)).pack(anchor="w", pady=(8, 0))
            self.play_buttons = [self.main_btn]
            return

        # 해상도 선택
        if m.resolutions:
            tk.Label(box, text="해상도", bg=C["panel"], fg=C["muted"], font=font(9, "bold"),
                     anchor="w").pack(fill="x")
            cur = m.state.get("resolution", m.default_resolution)
            label = dict(m.resolutions).get(cur, cur)
            self.res_btn = FlatButton(box, text=f"{label}   ▾", variant="card", weight="normal",
                                      anchor="w", pady=9, command=lambda: self._res_menu(m))
            self.res_btn.pack(fill="x", pady=(4, 14))

        self.play_buttons = []
        for i, (action, label) in enumerate(m.play_actions):
            row = tk.Frame(box, bg=C["panel"])
            row.pack(fill="x", pady=(0 if i == 0 else 8, 0))
            b = FlatButton(row, text=label, variant="primary" if i == 0 else "secondary",
                           size=16 if i == 0 else 13, pady=14 if i == 0 else 10,
                           command=lambda a=action: self.on_play(a))
            b.pack(side="left", fill="x", expand=True)
            if i == 0:
                self._gear(row, m)
            b.set_enabled(not self.busy and not running, "게임 실행 중" if running and i == 0 else None)
            self.play_buttons.append(b)

        ver = m.module_version()
        info = f"런처 v{ver}" if ver else "런처 확인 중"
        tk.Label(box, text=info, bg=C["panel"], fg=C["muted"], font=font(9), anchor="w").pack(fill="x", pady=(10, 0))
        path = tk.Label(box, text=f"설치 위치: {m.game_dir}", bg=C["panel"], fg=C["dim"], font=font(8),
                        anchor="w", cursor="hand2", wraplength=self.px(330), justify="left")
        path.pack(fill="x")
        path.bind("<ButtonRelease-1>", lambda e: open_folder(m.game_dir))
        Tooltip(path, "클릭하면 설치 폴더를 엽니다")

    def _gear(self, row, m):
        g = FlatButton(row, text="⚙", variant="primary", size=14, padx=14, pady=11,
                       command=lambda: self._gear_menu(g, m))
        g.pack(side="left", padx=(2, 0), fill="both")
        Tooltip(g, "설치 경로 설정")

    def _render_right(self, right, m):
        actions = tk.Frame(right, bg=C["bg"])
        actions.pack(fill="x")
        self.action_buttons = []
        for text, cmd in (("⟳  런처 업데이트 확인", self.on_check_update),
                          ("▶  리플레이", self.on_replay),
                          ("🗺  맵 에디터", self.on_map_editor)):
            b = FlatButton(actions, text=text, command=cmd, variant="card", size=10)
            b.pack(side="right", padx=(8, 0))
            b.set_enabled(m.available and not self.busy and (m.is_installed or "업데이트" in text))
            self.action_buttons.append(b)
        tk.Label(actions, text=m.name, bg=C["bg"], fg=C["text"], font=font(18, "bold")).pack(side="left")

        card = tk.Frame(right, bg=C["panel"])
        card.pack(fill="both", expand=True, pady=(14, 0))
        head = tk.Frame(card, bg=C["panel"], padx=24, pady=18)
        head.pack(fill="x")
        tk.Label(head, text="업데이트 내역", bg=C["panel"], fg=C["blue"], font=font(10, "bold"),
                 anchor="w").pack(fill="x")
        ver = m.module_version()
        title = f"{m.name} 런처 v{ver}" if ver else f"{m.name} 런처"
        tk.Label(head, text=title, bg=C["panel"], fg=C["text"], font=font(16, "bold"), anchor="w").pack(fill="x")
        tk.Frame(card, bg=C["line"], height=1).pack(fill="x", padx=24)
        wrap = tk.Frame(card, bg=C["panel"], padx=24, pady=14)
        wrap.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, style="Arena.Vertical.TScrollbar", orient="vertical")
        sb.pack(side="right", fill="y")
        text = tk.Text(wrap, bg=C["panel"], fg=C["text"], font=font(10), relief="flat", wrap="word",
                       highlightthickness=0, padx=0, pady=0, spacing1=2, spacing3=4,
                       yscrollcommand=sb.set, cursor="arrow")
        text.pack(fill="both", expand=True)
        sb.config(command=text.yview)
        text.tag_configure("ver", foreground=C["gold"], font=font(11, "bold"), spacing1=10)
        text.tag_configure("gap", font=font(3))
        for line in (m.notes() or "").splitlines():
            stripped = line.strip()
            is_ver = (stripped.startswith("v") and stripped[1:2].isdigit()) or "— v" in stripped
            text.insert("end", line + "\n", "ver" if is_ver else ("gap" if not stripped else ()))
        text.config(state="disabled")

    def _res_menu(self, m):
        menu = tk.Menu(self, tearoff=0, bg=C["card"], fg=C["text"], activebackground=C["blue"],
                       activeforeground="#fff", font=font(10), bd=0)
        for key, label in m.resolutions:
            menu.add_command(label=label, command=lambda k=key: self._set_res(m, k))
        b = self.res_btn
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _set_res(self, m, key):
        m.state["resolution"] = key
        m.save()
        self.render()

    def _gear_menu(self, btn, m):
        menu = tk.Menu(self, tearoff=0, bg=C["card"], fg=C["text"], activebackground=C["blue"],
                       activeforeground="#fff", font=font(10), bd=0)
        menu.add_command(label="설치 경로 변경…", command=self.on_change_path)
        if m.is_installed:
            menu.add_command(label="설치 폴더 열기", command=lambda: open_folder(m.game_dir))
            menu.add_separator()
            menu.add_command(label="게임 다시 설치…", command=lambda: self.on_install(reinstall=True))
        menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())

    # ── 백그라운드 작업 ───────────────────────────────────
    def run_task(self, label, fn, done=None, show_progress=True):
        """fn(progress, cancelled)을 스레드에서 실행하고 결과를 UI 스레드로 전달."""
        if self.busy:
            alert(self, "작업 중", "다른 작업이 진행 중입니다. 잠시 후 다시 시도하세요.", "warn")
            return
        self.busy = True
        self.cancel_flag = False
        self.status.set(label)
        if show_progress:
            self._show_progress(True)
        self.render()

        def progress(doneb, total, msg=None):
            self.events.put(("progress", doneb, total, msg))

        def worker():
            try:
                result = fn(progress, lambda: self.cancel_flag)
                self.events.put(("done", done, result, None))
            except Exception as exc:  # noqa: BLE001 — 사용자에게 그대로 안내
                self.events.put(("done", done, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "progress":
                    _, d, t, msg = ev
                    if msg:
                        self.status.set(msg)
                    if t:
                        self.progress.config(mode="determinate", value=int(d * 1000 / t))
                        self.pct.config(text=f"{d * 100 // t}%")
                        if not msg and d < t:
                            self.status.set(self.status.get().split("  ·")[0] + f"  ·  {d / 2**20:,.1f} / {t / 2**20:,.1f} MB")
                elif ev[0] == "done":
                    _, cb, result, exc = ev
                    self.busy = False
                    self._show_progress(False)
                    if isinstance(exc, Cancelled):
                        self.status.set("취소했습니다. 다시 시도하면 이어서 받습니다.")
                    elif exc is not None:
                        self.status.set("오류가 발생했습니다.")
                        kind = "info" if isinstance(exc, NotReady) else "error"
                        alert(self, "안내" if kind == "info" else "오류", str(exc), kind)
                    else:
                        self.status.set("준비 완료")
                    self.render()
                    if exc is None and cb:
                        cb(result)
        except queue.Empty:
            pass
        # 실행 중인 게임 감시
        for gid, proc in list(self.running.items()):
            if proc.poll() is not None:
                del self.running[gid]
                self.status.set("게임이 종료되었습니다.")
                if self.current.id == gid:
                    self.render()
        self.after(100, self._pump)

    def _show_progress(self, on):
        if on:
            self.progress.config(value=0)
            self.cancel_btn.pack(side="right", padx=(0, 10))
            self.pct.pack(side="right")
            self.progress.pack(side="right", padx=8)
            self.pct.config(text="")
        else:
            for w in (self.progress, self.pct, self.cancel_btn):
                w.pack_forget()

    def _request_cancel(self):
        self.cancel_flag = True
        self.status.set("취소하는 중…")

    # ── 시작 시 점검 ──────────────────────────────────────
    def _startup_checks(self):
        def work(progress, cancelled):
            self.catalog, online = updater.load_catalog(online=True)
            msgs = []
            if True:  # 카탈로그가 없어도 내장 주소로 게임 런처 확인은 시도
                for m in self.modules:
                    if m.available:
                        try:
                            msgs.append(m.check_module_update(progress))
                        except Exception as exc:  # noqa: BLE001
                            msgs.append(f"{m.name}: 업데이트 확인 실패 ({exc})")
            return online, msgs

        def done(result):
            online, msgs = result
            self.status.set(" / ".join(msgs) if msgs else "준비 완료")
            latest = updater.arena_update_available(self.catalog)
            if latest and confirm(self, "통합런처 업데이트",
                                  f"새 통합런처가 있습니다.\n\n현재 v{ARENA_VERSION}  →  최신 v{latest}\n\n지금 업데이트할까요?",
                                  "업데이트", "나중에"):
                self.run_task("통합런처 업데이트 받는 중…",
                              lambda p, c: updater.apply_arena_update(self.catalog, p),
                              lambda r: self.destroy())

        self.run_task("업데이트 확인 중…", work, done, show_progress=False)

    # ── 설치 ─────────────────────────────────────────────
    def on_install(self, reinstall=False):
        m = self.current
        if not reinstall:
            choice = choose(self, f"{m.name} 설치",
                            f"이 PC에 {m.name}가 이미 설치되어 있나요?",
                            [("have", "이미 설치되어 있어요",
                              "설치된 폴더만 지정하면 다운로드 없이 바로 사용합니다."),
                             ("new", "새로 설치할게요",
                              "NAS 서버에서 설치본을 받아 지정한 폴더에 압축을 풉니다.")])
            if choice is None:
                return
            if choice == "have":
                self.on_change_path()
                return
        self._install_new(m)

    def _install_new(self, m):
        url = m.catalog.get("setup_url")
        if not url:
            alert(self, "설치 불가", "설치본 주소가 등록되어 있지 않습니다.", "error")
            return

        def check_root(value):
            if not value:
                return "설치할 폴더를 입력하세요."
            if not os.path.isabs(value):
                return "전체 경로를 입력하세요. 예) C:\\Users\\이름\\Downloads"
            return None

        root = ask_folder(self, "설치 위치",
                          f"{m.name} 설치본을 압축 해제할 폴더입니다. 필요하면 다른 폴더로 바꿀 수 있습니다.",
                          initial=str(default_install_root()), button="설치 시작", must=check_root)
        if not root:
            return

        def work(progress, cancelled):
            from pathlib import Path
            zip_path = app_data_dir() / "downloads" / f"{m.id}_setup.zip"
            progress(0, 0, f"{m.name} 설치본 받는 중…")
            download(url, zip_path, progress, cancelled, expect_zip=True)
            target, top, _ = plan(zip_path, Path(root), m.fallback_folder)
            progress(0, 0, "압축 해제 중…")
            extract(zip_path, target, lambda d, t: progress(d, t, None), cancelled)
            search_from = target / top if top else target
            game_dir = find_dir_with(search_from, m.required_files)
            if not game_dir:
                raise RuntimeError("압축은 풀었지만 게임 실행 파일(" + ", ".join(m.required_files) +
                                   f")을 찾지 못했습니다.\n위치: {search_from}")
            m.set_game_dir(game_dir)
            zip_path.unlink(missing_ok=True)
            if getattr(m, "module_exe", "n/a") is None:
                progress(0, 0, "게임 런처 준비 중…")
                m.check_module_update(progress)
            return game_dir

        self.run_task(f"{m.name} 설치 준비 중…", work,
                      lambda d: alert(self, "설치 완료", f"{m.name} 설치가 끝났습니다.\n\n설치 위치: {d}", "ok"))

    def on_change_path(self):
        m = self.current

        def must(value):
            return None if m.validate(value) else (
                "이 폴더에서 " + ", ".join(m.required_files) + " 파일을 찾지 못했습니다.")

        path = ask_folder(self, "설치 경로 지정",
                          f"{m.name}가 설치된 폴더를 선택하세요. (" + ", ".join(m.required_files) + " 이 있는 폴더)",
                          initial=m.guess_existing_dir() or str(default_install_root()), must=must)
        if path:
            m.set_game_dir(path)
            self.status.set(f"설치 경로를 저장했습니다: {m.game_dir}")
            self.render()

    # ── 실행 ─────────────────────────────────────────────
    def on_play(self, action):
        m = self.current
        if m.id in self.running:
            return
        res = m.state.get("resolution", m.default_resolution)

        def work(progress, cancelled):
            # 실행 직전 최신 런처 확인 (오프라인이면 받아 둔 런처로 진행)
            try:
                self.catalog, _ = updater.load_catalog(online=True)
                m.check_module_update(progress)
            except Exception:
                if not getattr(m, "module_exe", None):
                    raise
            progress(0, 0, f"{m.name} 실행 준비 중…")
            return m.launch(action, res)

        def started(proc):
            self.running[m.id] = proc
            self.status.set(f"{m.name} 실행 중 — 게임을 종료하면 다시 시작할 수 있습니다.")

        self.run_task(f"{m.name} 실행 준비 중…", work, started, show_progress=False)

    def on_check_update(self):
        m = self.current

        def work(progress, cancelled):
            self.catalog, _ = updater.load_catalog(online=True)
            try:
                return m.check_module_update(progress)
            except OSError as exc:
                raise RuntimeError(f"업데이트 서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.\n({exc})")

        def done(msg):
            alert(self, "런처 업데이트", msg, "ok")
            latest = updater.arena_update_available(self.catalog)
            if latest and confirm(self, "통합런처 업데이트",
                                  f"새 통합런처 v{latest}가 있습니다. 지금 업데이트할까요?", "업데이트", "나중에"):
                self.run_task("통합런처 업데이트 받는 중…",
                              lambda p, c: updater.apply_arena_update(self.catalog, p),
                              lambda r: self.destroy())

        self.run_task("런처 업데이트 확인 중…", work, done)

    def on_replay(self):
        try:
            self.current.open_replay()
            self.status.set("리플레이를 실행했습니다.")
        except NotReady as exc:
            alert(self, "리플레이", str(exc))
        except Exception as exc:  # noqa: BLE001
            alert(self, "리플레이", str(exc), "error")

    def on_map_editor(self):
        m = self.current
        self.run_task("맵 에디터 준비 중…", lambda p, c: m.open_map_editor(p),
                      lambda r: self.status.set("맵 에디터를 실행했습니다."))


def main():
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    relaunch_as_admin()
    updater.cleanup_old_binaries()
    ArenaApp().mainloop()


if __name__ == "__main__":
    main()
