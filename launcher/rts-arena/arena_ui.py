"""RTS-Arena 공용 UI 위젯 (다크 테마, 평면 버튼, 다이얼로그)."""
import os
import tkinter as tk
from tkinter import filedialog

FONT = "맑은 고딕"

C = dict(
    bg="#0f1117", top="#0b0d12", strip="#181b23", panel="#151820", card="#1d212b",
    card_hi="#262b37", line="#2a2f3b", text="#eceef3", muted="#8c93a3", dim="#5b6172",
    blue="#148eff", blue_hi="#3aa2ff", blue_dn="#0b73d6", blue2="#1f3b63", blue2_hi="#274b7d",
    green="#3ecf6e", red="#ff5d5d", gold="#f5b83d",
)


def font(size=10, weight="normal"):
    return (FONT, size, weight)


class FlatButton(tk.Label):
    """배경색만으로 표현하는 평면 버튼. variant: primary/secondary/ghost/card."""
    STYLES = {
        "primary": (C["blue"], C["blue_hi"], "#ffffff"),
        "secondary": (C["blue2"], C["blue2_hi"], "#ffffff"),
        "card": (C["card"], C["card_hi"], C["text"]),
        "ghost": (C["top"], C["card"], C["text"]),
        "strip": (C["strip"], C["card"], C["text"]),
    }

    def __init__(self, master, text="", command=None, variant="card", size=10, weight="bold",
                 padx=14, pady=7, image=None, **kw):
        bg, hover, fg = self.STYLES[variant]
        super().__init__(master, text=text, bg=bg, fg=fg, font=font(size, weight), padx=padx, pady=pady,
                         cursor="hand2", image=image, compound="left", **kw)
        self._bg, self._hover, self._fg = bg, hover, fg
        self.command = command
        self.enabled = True
        self.bind("<Enter>", lambda e: self.enabled and self.config(bg=self._hover))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg if self.enabled else self._disabled_bg()))
        self.bind("<ButtonRelease-1>", self._click)

    def _disabled_bg(self):
        return C["card"] if self._bg in (C["blue"], C["blue2"]) else self._bg

    def _click(self, _e):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, on: bool, text=None):
        self.enabled = on
        if text is not None:
            self.config(text=text)
        self.config(bg=self._bg if on else self._disabled_bg(), fg=self._fg if on else C["dim"],
                    cursor="hand2" if on else "arrow")


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=self.text, bg="#000000", fg=C["text"], font=font(9),
                 padx=8, pady=4).pack()
        self.tip.update_idletasks()
        self.tip.geometry(f"+{x - self.tip.winfo_width() // 2}+{y}")

    def hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Dialog(tk.Toplevel):
    """다크 테마 모달 다이얼로그 기반."""

    def __init__(self, master, title, width=460):
        super().__init__(master, bg=C["panel"])
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        self.body = tk.Frame(self, bg=C["panel"], padx=24, pady=20)
        self.body.pack(fill="both", expand=True)
        self.buttons = tk.Frame(self, bg=C["panel"], padx=24, pady=(0))
        self.buttons.pack(fill="x", pady=(0, 18))
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self._width = width

    def heading(self, text, sub=None):
        tk.Label(self.body, text=text, bg=C["panel"], fg=C["text"], font=font(13, "bold"),
                 anchor="w", justify="left", wraplength=self._width - 48).pack(fill="x")
        if sub:
            tk.Label(self.body, text=sub, bg=C["panel"], fg=C["muted"], font=font(10), anchor="w",
                     justify="left", wraplength=self._width - 48).pack(fill="x", pady=(6, 0))

    def add_button(self, text, command, variant="card"):
        b = FlatButton(self.buttons, text=text, command=command, variant=variant, padx=18, pady=8)
        b.pack(side="right", padx=(8, 0))
        return b

    def run(self):
        self.update_idletasks()
        m = self.master.winfo_toplevel()
        w = max(self._width, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        x = m.winfo_rootx() + (m.winfo_width() - w) // 2
        y = m.winfo_rooty() + (m.winfo_height() - h) // 3
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result

    def done(self, value):
        self.result = value
        self.destroy()

    def cancel(self):
        self.done(None)


def alert(master, title, message, kind="info"):
    d = Dialog(master, title)
    color = {"info": C["text"], "error": C["red"], "warn": C["gold"], "ok": C["green"]}[kind]
    tk.Label(d.body, text=title, bg=C["panel"], fg=color, font=font(13, "bold"), anchor="w").pack(fill="x")
    tk.Label(d.body, text=message, bg=C["panel"], fg=C["text"], font=font(10), justify="left",
             anchor="w", wraplength=410).pack(fill="x", pady=(8, 0))
    d.add_button("확인", lambda: d.done(True), "primary")
    d.bind("<Return>", lambda e: d.done(True))
    return d.run()


def confirm(master, title, message, yes="예", no="아니오"):
    d = Dialog(master, title)
    d.heading(title, message)
    d.add_button(no, lambda: d.done(False))
    d.add_button(yes, lambda: d.done(True), "primary")
    return bool(d.run())


def choose(master, title, message, options):
    """큰 선택지 카드 목록. options=[(value, 제목, 설명)]"""
    d = Dialog(master, title, width=500)
    d.heading(title, message)
    for value, label, desc in options:
        card = tk.Frame(d.body, bg=C["card"], cursor="hand2", padx=16, pady=12)
        card.pack(fill="x", pady=(12, 0))
        t = tk.Label(card, text=label, bg=C["card"], fg=C["text"], font=font(11, "bold"), anchor="w")
        t.pack(fill="x")
        s = tk.Label(card, text=desc, bg=C["card"], fg=C["muted"], font=font(9), anchor="w",
                     justify="left", wraplength=420)
        s.pack(fill="x", pady=(2, 0))
        widgets = (card, t, s)
        for w in widgets:
            w.bind("<Enter>", lambda e, ws=widgets: [x.config(bg=C["card_hi"]) for x in ws])
            w.bind("<Leave>", lambda e, ws=widgets: [x.config(bg=C["card"]) for x in ws])
            w.bind("<ButtonRelease-1>", lambda e, v=value: d.done(v))
    d.add_button("취소", d.cancel)
    return d.run()


def ask_folder(master, title, message, initial="", button="확인", must=None):
    """경로 입력 + 찾기. must(path)->오류메시지|None 로 검증."""
    d = Dialog(master, title, width=560)
    d.heading(title, message)
    row = tk.Frame(d.body, bg=C["panel"])
    row.pack(fill="x", pady=(14, 0))
    var = tk.StringVar(value=initial)
    entry = tk.Entry(row, textvariable=var, bg=C["card"], fg=C["text"], insertbackground=C["text"],
                     relief="flat", font=font(10), highlightthickness=1, highlightbackground=C["line"],
                     highlightcolor=C["blue"])
    entry.pack(side="left", fill="x", expand=True, ipady=7)

    def browse():
        path = filedialog.askdirectory(parent=d, initialdir=var.get() or None, title=title)
        if path:
            var.set(os.path.normpath(path))

    FlatButton(row, text="찾기", command=browse, padx=14, pady=6).pack(side="left", padx=(8, 0))
    err = tk.Label(d.body, text="", bg=C["panel"], fg=C["red"], font=font(9), anchor="w",
                   justify="left", wraplength=500)
    err.pack(fill="x", pady=(6, 0))

    def ok(_e=None):
        value = var.get().strip().strip('"')
        problem = must(value) if must else (None if value else "경로를 입력하세요.")
        if problem:
            err.config(text=problem)
            return
        d.done(value)

    d.add_button("취소", d.cancel)
    d.add_button(button, ok, "primary")
    entry.bind("<Return>", ok)
    entry.focus_set()
    return d.run()


def ask_text(master, title, message, initial="", button="확인", validate=None):
    d = Dialog(master, title)
    d.heading(title, message)
    var = tk.StringVar(value=initial)
    entry = tk.Entry(d.body, textvariable=var, bg=C["card"], fg=C["text"], insertbackground=C["text"],
                     relief="flat", font=font(12), highlightthickness=1, highlightbackground=C["line"],
                     highlightcolor=C["blue"])
    entry.pack(fill="x", pady=(14, 0), ipady=7)
    err = tk.Label(d.body, text="", bg=C["panel"], fg=C["red"], font=font(9), anchor="w")
    err.pack(fill="x", pady=(6, 0))

    def ok(_e=None):
        value = var.get().strip()
        problem = validate(value) if validate else None
        if problem:
            err.config(text=problem)
            return
        d.done(value)

    d.add_button("취소", d.cancel)
    d.add_button(button, ok, "primary")
    entry.bind("<Return>", ok)
    entry.focus_set()
    entry.select_range(0, "end")
    return d.run()
