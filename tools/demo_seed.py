"""화면 확인용 가짜 경기 만들기: python tools/demo_seed.py [데이터폴더(기본 data-demo)]
그 뒤:  set ARENA_DATA_DIR=data-demo  &&  python -m arena   → http://127.0.0.1:8080
"""
import datetime as dt
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from arena.db import Database  # noqa: E402
from arena.store import Store  # noqa: E402
from test_arena import make_rkr  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data-demo")
store = Store(Database(out / "arena.sqlite3"), out / "replays")
names = ["용사", "바람검", "Frog", "aa", "마르두크장인", "아렌사랑"]
maps = ["[2]Distant", "[2]The Final", "[4]Cross Road", "[4]녹아웃"]
rng = random.Random(7)
t0 = dt.datetime.now() - dt.timedelta(days=3)
for i in range(30):
    a, b = rng.sample(names, 2)
    when = t0 + dt.timedelta(minutes=37 * i)
    win = rng.randint(0, 1)
    races = [rng.randint(0, 2), rng.randint(0, 2)] + [3] * 6
    data = make_rkr([(0, a), (1, b)], [32, 32] + [0] * 6, races=races, date=when.isoformat(timespec="seconds"),
                    seed=rng.randint(1, 10 ** 9), mapname=f"DATA\\MAPS\\{rng.choice(maps)}.rkm",
                    final_buildings={0: 5 if win == 0 else 0, 1: 5 if win == 1 else 0},
                    duration=rng.randint(240, 1500))
    print(store.ingest("rakmu", data, f"{when:%Y%m%d_%H%M%S}.rkr", a).status)
