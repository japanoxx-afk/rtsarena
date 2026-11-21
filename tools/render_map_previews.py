"""라크무 맵 미리보기 PNG + 맵 목록(rakmu_maps.json) 만들기 — 게임이 설치된 PC 에서 한 번 실행해 결과를 커밋한다.

사이트(NAS)는 게임 파일·Tk 가 없으므로 미리 그린 그림만 내보낸다.
그리는 방법은 런처 '맵 미리보기' 탭(new-rakum map_preview.render_tk)과 같다: 실제 지형 타일·언덕 입구·금광/우물 그림.
그 위에 시작 위치를 플레이어 색 네모로 표시한다.

    python tools/render_map_previews.py --rakum C:\\...\\new-rakum [--game "C:\\Program Files (x86)\\TriggerSoft\\RhakMu"]

새 맵이 생기면 다시 실행하고, arena/static/maps/rakmu/ 와 arena/games/rakmu_maps.json 을 커밋한 뒤 NAS 에 올린다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from arena.games.rakmu import map_display  # noqa: E402

OUT_IMG = ROOT / "arena" / "static" / "maps" / "rakmu"
OUT_JSON = ROOT / "arena" / "games" / "rakmu_maps.json"
MAX_PX = 480


def image_name(display: str) -> str:
    """주소에 한글·괄호가 들어가지 않게 이름의 해시로 파일 이름을 정한다 (사이트 쪽 arena.games.rakmu.map_image 와 같은 규칙)."""
    return hashlib.sha1(display.encode("utf-8")).hexdigest()[:12] + ".png"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rakum", required=True, help="new-rakum 저장소 폴더 (map_preview.py, map_preview_tiles/)")
    ap.add_argument("--game", default=r"C:\Program Files (x86)\TriggerSoft\RhakMu")
    a = ap.parse_args()
    sys.path.insert(0, a.rakum)
    import tkinter as tk
    import map_preview as mp

    root = tk.Tk()
    root.withdraw()
    OUT_IMG.mkdir(parents=True, exist_ok=True)
    catalog = []
    for rel in mp.list_maps(a.game):
        path = Path(a.game) / "Data" / "Maps" / rel
        try:
            info = mp.load(path)
            img, scale = mp.render_tk(info, a.rakum, root, MAX_PX)
        except Exception as e:  # noqa: BLE001
            print(f"건너뜀 {rel}: {e}")
            continue
        w, h = img.width(), img.height()
        for i, (x, y) in enumerate(info.players):               # 시작 위치
            px, py = mp.to_px_scaled(scale, x, y)
            px, py = int(px), int(py)
            r = 6
            img.put("#000000", to=(max(0, px - r - 1), max(0, py - r - 1), min(w, px + r + 1), min(h, py + r + 1)))
            img.put(mp.PLAYER_COLORS[i % 8], to=(max(0, px - r), max(0, py - r), min(w, px + r), min(h, py + r)))
        name = map_display(Path(rel).name)
        fn = image_name(name)
        img.write(str(OUT_IMG / fn), format="png")
        pw, ph = info.playable
        catalog.append({
            "name": name, "file": rel.replace("\\", "/"), "image": fn, "width": w, "height": h,
            "players": len(info.players), "tileset": info.tileset_name, "size": [pw, ph],
            "gold": len(info.gold), "gold_total": sum(v for *_, v in info.gold), "wells": len(info.wells),
            "official": "\\" not in rel and "/" not in rel,
        })
        print(f"{name:32s} {w}x{h}  {fn}")
    root.destroy()
    catalog.sort(key=lambda m: (not m["official"], m["players"], m["name"].lower()))
    OUT_JSON.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(catalog)}개 → {OUT_JSON}")


if __name__ == "__main__":
    main()
