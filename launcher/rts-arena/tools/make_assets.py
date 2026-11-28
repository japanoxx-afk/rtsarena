"""아이콘/로고 PNG 생성기 (개발용). 결과는 assets/ 에 저장되어 exe에 포함된다.

    python tools/make_assets.py
"""
import glob
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets"
OUT.mkdir(exist_ok=True)
SS = 4  # 슈퍼샘플링


def font(size, weight="Black"):
    pats = [f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc",
            "C:/Windows/Fonts/malgunbd.ttf"]
    for p in pats:
        for f in glob.glob(p):
            try:
                return ImageFont.truetype(f, size, index=1) if f.endswith(".ttc") else ImageFont.truetype(f, size)
            except OSError:
                continue
    return ImageFont.load_default()


def vgrad(w, h, top, bottom):
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    return img


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


def centered_text(img, text, fnt, fill, dy=0, stroke=0, stroke_fill=None):
    d = ImageDraw.Draw(img)
    box = d.textbbox((0, 0), text, font=fnt, stroke_width=stroke)
    x = (img.width - (box[2] - box[0])) / 2 - box[0]
    y = (img.height - (box[3] - box[1])) / 2 - box[1] + dy
    d.text((x, y), text, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def gold_text(size, text, fnt, colors=((255, 226, 140), (196, 120, 24)), stroke=(40, 14, 4)):
    """금속 느낌 그라데이션 글자 + 외곽선 + 그림자."""
    w, h = size
    mask = Image.new("L", size, 0)
    centered_text(mask, text, fnt, 255)
    fill = vgrad(w, h, colors[0], colors[1])
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    outline = Image.new("L", size, 0)
    centered_text(outline, text, fnt, 255, stroke=max(2, fnt.size // 14), stroke_fill=255)
    shadow = outline.filter(ImageFilter.GaussianBlur(fnt.size // 10))
    out.paste((0, 0, 0, 200), (0, 0), shadow)
    out.paste(stroke + (255,), (0, 0), outline)
    out.paste(fill, (0, 0), mask)
    return out


GAMES = {
    "rhakmu": dict(glyph="라", title="라크무", sub="RHAKMU", c1=(150, 22, 30), c2=(48, 6, 12), ring=(232, 176, 70)),
    "wanggeon": dict(glyph="왕", title="태조왕건", sub="TAEJO WANGGEON", c1=(28, 70, 150), c2=(8, 20, 54), ring=(214, 190, 120)),
    "cheonmyeong2": dict(glyph="천", title="삼국지 천명2", sub="CHEONMYEONG II", c1=(26, 110, 70), c2=(6, 34, 22), ring=(220, 196, 120)),
}


def make_icon(gid, g, size=56):
    S = size * SS
    base = vgrad(S, S, g["c1"], g["c2"])
    d = ImageDraw.Draw(base)
    # 방패형 테두리
    pad = S * 0.08
    d.rounded_rectangle([pad, pad, S - pad, S - pad], S * 0.2, outline=g["ring"] + (255,), width=int(S * 0.045))
    hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))  # 위쪽 하이라이트
    ImageDraw.Draw(hl).ellipse([-S * 0.3, -S * 0.9, S * 1.3, S * 0.45], fill=(255, 255, 255, 30))
    base.alpha_composite(hl)
    glyph = gold_text((S, S), g["glyph"], font(int(S * 0.58)), stroke=(30, 8, 4))
    base.alpha_composite(glyph, (0, int(-S * 0.02)))
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(base, (0, 0), rounded_mask(S, S, int(S * 0.22)))
    out.resize((size, size), Image.LANCZOS).save(OUT / f"icon_{gid}.png")
    return out


def make_logo(gid, g, w=340, h=190):
    W, H = w * SS, h * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.12, H * 0.12, W * 0.88, H * 0.86], fill=g["c1"] + (150,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(W // 14)))
    title_font = font(int(H * (0.42 if len(g["title"]) <= 4 else 0.3)))
    title = gold_text((W, int(H * 0.7)), g["title"], title_font)
    img.alpha_composite(title, (0, 0))
    sub = gold_text((W, int(H * 0.3)), g["sub"], font(int(H * 0.13), "Bold"),
                    colors=((240, 232, 220), (170, 160, 150)), stroke=(20, 10, 8))
    img.alpha_composite(sub, (0, int(H * 0.64)))
    d = ImageDraw.Draw(img)
    y = int(H * 0.68)
    d.line([(W * 0.18, y), (W * 0.82, y)], fill=g["ring"] + (200,), width=SS * 2)
    img.resize((w, h), Image.LANCZOS).save(OUT / f"logo_{gid}.png")


def make_social():
    S = 30 * SS
    # 유튜브: 빨간 둥근 사각형 + 재생 삼각형
    yt = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(yt)
    d.rounded_rectangle([0, S * 0.17, S - 1, S * 0.83], S * 0.2, fill=(255, 0, 0, 255))
    d.polygon([(S * 0.4, S * 0.33), (S * 0.4, S * 0.67), (S * 0.68, S * 0.5)], fill="white")
    yt.resize((30, 30), Image.LANCZOS).save(OUT / "youtube.png")
    # 디스코드: 블러플 원 + 단순화한 게임패드 얼굴
    dc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(dc)
    d.ellipse([0, 0, S - 1, S - 1], fill=(88, 101, 242, 255))
    d.rounded_rectangle([S * 0.2, S * 0.3, S * 0.8, S * 0.72], S * 0.16, fill="white")
    d.polygon([(S * 0.2, S * 0.55), (S * 0.14, S * 0.72), (S * 0.32, S * 0.68)], fill="white")
    d.polygon([(S * 0.8, S * 0.55), (S * 0.86, S * 0.72), (S * 0.68, S * 0.68)], fill="white")
    for cx in (0.39, 0.61):
        d.ellipse([S * (cx - 0.06), S * 0.43, S * (cx + 0.06), S * 0.57], fill=(88, 101, 242, 255))
    dc.resize((30, 30), Image.LANCZOS).save(OUT / "discord.png")


def make_arena_logo():
    W, H = 220 * SS, 40 * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 교차한 두 검 (단순 도형)
    cx, cy, L = H * 0.5, H * 0.5, H * 0.42
    for sgn in (1, -1):
        a = math.radians(45 * sgn)
        dx, dy = math.cos(a) * L, math.sin(a) * L
        d.line([(cx - dx, cy + dy), (cx + dx, cy - dy)], fill=(20, 142, 255, 255), width=int(H * 0.11))
        gx, gy = cx - dx * 0.55, cy + dy * 0.55
        d.line([(gx - dy * 0.35, gy - dx * 0.35), (gx + dy * 0.35, gy + dx * 0.35)], fill=(245, 184, 61, 255), width=int(H * 0.09))
    f = font(int(H * 0.62))
    d.text((H * 1.15, H * 0.5), "RTS", font=f, fill=(255, 255, 255, 255), anchor="lm")
    x = H * 1.15 + d.textlength("RTS", font=f) + H * 0.12
    d.text((x, H * 0.5), "ARENA", font=f, fill=(20, 142, 255, 255), anchor="lm")
    img.resize((220, 40), Image.LANCZOS).save(OUT / "arena_logo.png")

    # 실행 파일 아이콘
    S = 256
    ico = vgrad(S, S, (24, 34, 60), (8, 10, 18))
    d = ImageDraw.Draw(ico)
    cx = cy = S / 2
    L = S * 0.36
    for sgn in (1, -1):
        a = math.radians(45 * sgn)
        dx, dy = math.cos(a) * L, math.sin(a) * L
        d.line([(cx - dx, cy + dy), (cx + dx, cy - dy)], fill=(20, 142, 255), width=int(S * 0.09))
        gx, gy = cx - dx * 0.55, cy + dy * 0.55
        d.line([(gx - dy * 0.35, gy - dx * 0.35), (gx + dy * 0.35, gy + dx * 0.35)], fill=(245, 184, 61), width=int(S * 0.07))
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(ico, (0, 0), rounded_mask(S, S, int(S * 0.22)))
    out.save(OUT / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    out.resize((64, 64), Image.LANCZOS).save(OUT / "app.png")


if __name__ == "__main__":
    for gid, g in GAMES.items():
        if gid == "rhakmu" and (OUT / "icon_rhakmu_256.png").exists():
            pass  # 라크무 아이콘은 원본 게임 아이콘(EDSR 업스케일)을 사용 — 덮어쓰지 않음
        else:
            make_icon(gid, g)
        make_logo(gid, g)
    make_social()
    make_arena_logo()
    print("assets:", sorted(p.name for p in OUT.iterdir()))
