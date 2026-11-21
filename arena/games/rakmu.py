"""라크무(RhakMu) 리플레이(.rkr) 해석.

.rkr = gzip 으로 압축한 JSON 줄 (라크무 런처 replay_recorder.py 가 만든다).
  1줄: 머리 {"format", "date", "map", "players": [{"index","name","race"}], "races": [8], "slots": [8], "seed", ...}
  나머지: 장면 {"t": 경기 초, "k": [유닛 전체] | "c": [바뀐 유닛], "x": [사라진 번호], ...}
  유닛 = [번호, 종류, 소유(플레이어 번호), x, y, 체력, 표시(1=건물)]

slots 값 (게임 메모리 플레이어 구조 +6, 실제 리플레이로 확인):
  0x20(32) = 사람, 0x10(16) = 컴퓨터, 0 = 빈 자리
  → 컴퓨터와 혼자 한 경기(싱글)는 사람이 1명이라 전적·리플레이를 저장하지 않는다.
"""
from __future__ import annotations

import gzip
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

KEY = "rakmu"
TITLE = "라크무"
EXT = ".rkr"

RACES = ("무휼", "아렌", "마르두크")
RACE_COLORS = ("#e8944a", "#4a90e2", "#b46ae0")
RACE_LETTERS = ("무", "아", "마")
SLOT_HUMAN, SLOT_COMPUTER = 0x20, 0x10
F_BUILDING = 1

MAX_REPLAY_BYTES = 20 * 1024 * 1024
MAX_LINES = 400_000


class ReplayError(ValueError):
    """리플레이를 받을 수 없음 (메시지는 런처에 그대로 보여 준다)."""


@dataclass
class PlayerInfo:
    index: int
    name: str
    race: int | None
    human: bool = True


@dataclass
class ReplayInfo:
    date: str                 # ISO "2026-11-21T02:41:02"
    map_path: str
    seed: int | None
    duration: float
    players: list = field(default_factory=list)       # 사람만 (PlayerInfo)
    computers: int = 0
    human_slots: int = 0
    buildings_left: dict = field(default_factory=dict)  # 플레이어 번호 -> 마지막 장면의 건물 수
    units_left: dict = field(default_factory=dict)
    # 플레이어 번호 -> {"peak_army": 동시에 가장 많았던 병력(건물 아닌 유닛) 수,
    #                   "units_made": 한 번이라도 나온 유닛 수, "buildings_made": 한 번이라도 나온 건물 수}
    player_stats: dict = field(default_factory=dict)

    @property
    def map_name(self) -> str:
        return map_display(self.map_path)

    def inferred_results(self) -> dict:
        """마지막 장면에서 건물이 남은 사람이 1명뿐이면 그 사람 승, 나머지 패 (추정)."""
        idx = [p.index for p in self.players]
        alive = [i for i in idx if self.buildings_left.get(i, 0) > 0]
        if len(alive) == 1 and len(idx) >= 2:
            return {i: ("win" if i == alive[0] else "lose") for i in idx}
        return {}


def race_name(race) -> str:
    return RACES[race] if isinstance(race, int) and 0 <= race < len(RACES) else "랜덤/기타"


def map_display(path: str) -> str:
    """'DATA\\MAPS\\[2]Distant.rkm' -> '(2) Distant'."""
    base = re.split(r"[\\/]", path or "")[-1]
    base = re.sub(r"\.(rkm|j2m|wgm)$", "", base, flags=re.I)
    m = re.match(r"^\[(\d+)\]\s*(.*)$", base)
    if m:
        return f"({m.group(1)}) {m.group(2).strip()}"
    return base or "알 수 없는 맵"


# ---------------------------------------------------------------- 맵 목록·미리보기 (tools/render_map_previews.py 가 만든다)
_CATALOG_FILE = Path(__file__).with_name("rakmu_maps.json")
_catalog: list | None = None


def map_catalog() -> list[dict]:
    """[{name, file, image, width, height, players, tileset, size, gold, gold_total, wells, official}]"""
    global _catalog
    if _catalog is None:
        try:
            _catalog = json.loads(_CATALOG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalog = []
    return _catalog


def map_info(name: str) -> dict | None:
    for m in map_catalog():
        if m["name"] == name:
            return m
    low = (name or "").lower()
    for m in map_catalog():
        if m["name"].lower() == low:
            return m
    return None


def map_image(name: str) -> str:
    """미리보기 그림 주소 (없으면 빈 문자열)."""
    m = map_info(name)
    return f"/static/maps/rakmu/{m['image']}" if m else ""


def _open_lines(data: bytes):
    try:
        text = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_REPLAY_BYTES * 8)
    except (OSError, EOFError) as e:
        raise ReplayError(f"리플레이 파일이 아닙니다 (gzip 오류: {e})")
    try:
        return text.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise ReplayError("리플레이 내용이 UTF-8 JSON 이 아닙니다")


def parse_replay(data: bytes) -> ReplayInfo:
    if len(data) > MAX_REPLAY_BYTES:
        raise ReplayError("리플레이 파일이 너무 큽니다")
    lines = _open_lines(data)
    if not lines:
        raise ReplayError("빈 리플레이입니다")
    try:
        head = json.loads(lines[0])
    except ValueError:
        raise ReplayError("리플레이 머리를 읽을 수 없습니다")
    if not isinstance(head, dict) or "map" not in head or "players" not in head:
        raise ReplayError("라크무 리플레이 머리 형식이 아닙니다")

    slots = head.get("slots") or []
    races = head.get("races") or []
    named = {}
    for p in head.get("players") or []:
        try:
            i = int(p.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        name = str(p.get("name") or "").strip()
        if name:
            named[i] = PlayerInfo(i, name[:32], p.get("race") if isinstance(p.get("race"), int) else None)

    players, computers, human_slots = [], 0, 0
    if slots:
        for i, s in enumerate(slots[:8]):
            if s == SLOT_COMPUTER:
                computers += 1
            elif s == SLOT_HUMAN:
                human_slots += 1
                pi = named.get(i) or PlayerInfo(i, "", races[i] if i < len(races) and isinstance(races[i], int) else None)
                players.append(pi)
        # slots 에 없는데 이름이 있는 사람(예전 형식) 보충
        for i, pi in named.items():
            if i < len(slots) and slots[i] == SLOT_COMPUTER:
                continue
            if all(p.index != i for p in players):
                players.append(pi)
                human_slots += 1
    else:  # slots 가 없는 옛 리플레이: 이름 있는 참가자를 사람으로 본다
        players = sorted(named.values(), key=lambda p: p.index)
        human_slots = len(players)
    players.sort(key=lambda p: p.index)

    units: dict = {}
    army: dict = {}                    # 소유자 -> 지금 병력 수
    peak: dict = {}
    made_units: dict = {}              # 소유자 -> 본 유닛 번호들
    made_bld: dict = {}

    def _add(r):
        o, b = r[2], r[6] & F_BUILDING
        (made_bld if b else made_units).setdefault(o, set()).add(r[0])
        if not b:
            army[o] = army.get(o, 0) + 1

    def _remove(r):
        if not (r[6] & F_BUILDING):
            army[r[2]] = army.get(r[2], 0) - 1

    last_t = 0.0
    for n, line in enumerate(lines[1:]):
        if n > MAX_LINES:
            raise ReplayError("리플레이 장면이 너무 많습니다")
        if not line:
            continue
        try:
            fr = json.loads(line)
        except ValueError:
            continue  # 마지막 줄이 잘린 기록(게임 강제 종료)은 무시
        t = fr.get("t")
        if isinstance(t, (int, float)) and t > last_t:
            last_t = float(t)
        if "k" in fr:
            units = {r[0]: r for r in fr["k"] if isinstance(r, list) and len(r) >= 7}
            army.clear()
            for r in units.values():
                _add(r)
        for r in fr.get("c", ()) or ():
            if isinstance(r, list) and len(r) >= 7:
                old_r = units.get(r[0])
                if old_r is not None:
                    _remove(old_r)
                units[r[0]] = r
                _add(r)
        for x in fr.get("x", ()) or ():
            old_r = units.pop(x, None)
            if old_r is not None:
                _remove(old_r)
        for o, n in army.items():
            if n > peak.get(o, 0):
                peak[o] = n

    buildings, alive = {}, {}
    for r in units.values():
        owner = r[2]
        alive[owner] = alive.get(owner, 0) + 1
        if r[6] & F_BUILDING:
            buildings[owner] = buildings.get(owner, 0) + 1

    seed = head.get("seed")
    return ReplayInfo(
        date=str(head.get("date") or ""),
        map_path=str(head.get("map") or ""),
        seed=seed if isinstance(seed, int) else None,
        duration=round(last_t, 2),
        players=players,
        computers=computers,
        human_slots=human_slots,
        buildings_left=buildings,
        units_left=alive,
        player_stats={p.index: {"peak_army": peak.get(p.index, 0),
                                "units_made": len(made_units.get(p.index, ())),
                                "buildings_made": len(made_bld.get(p.index, ()))} for p in players},
    )


def is_multiplayer(info: ReplayInfo) -> bool:
    """사람 2명 이상이 참가한 경기만 전적으로 친다 (나 + 컴퓨터만 있는 싱글은 제외)."""
    return info.human_slots >= 2


def check_header_only(data: bytes) -> tuple[bool, str]:
    """런처용 빠른 검사: 머리 줄만 읽고 대전(사람 2명 이상) 여부를 돌려준다."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            head = json.loads(g.readline().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - 어떤 손상이든 '올리지 않음'
        return False, f"읽기 실패: {e}"
    slots = head.get("slots")
    if slots:
        humans = sum(1 for s in slots if s == SLOT_HUMAN)
    else:
        humans = sum(1 for p in head.get("players") or [] if (p or {}).get("name"))
    if humans < 2:
        return False, "싱글(사람 1명 + 컴퓨터) 경기"
    return True, f"사람 {humans}명"
