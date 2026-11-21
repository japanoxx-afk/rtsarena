"""HTTP 서버: 전적 페이지(서버에서 HTML 생성) + 런처 업로드 API + 리플레이 다운로드."""
from __future__ import annotations

import hmac
import json
import logging
import math
import re
import threading
import urllib.parse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import games
from .config import Config
from .db import Database
from .games import rakmu
from .rakmu_server import RakmuServerSource
from .store import Store

log = logging.getLogger("arena.web")
SYNC_STATUS: dict = {"enabled": False}   # /healthz 에 보여 줌 (NAS 로그를 못 볼 때 원인 확인용)
STATIC = Path(__file__).resolve().parent / "static"
PER_PAGE = 20


def e(s) -> str:
    return escape("" if s is None else str(s), quote=True)


def q(s) -> str:
    return urllib.parse.quote(str(s), safe="")


def fmt_dur(sec) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}분 {sec % 60}초"


# ---------------------------------------------------------------- 공통 틀
NAV = [("", "리더보드"), ("matches", "전적기록"), ("stats", "게임통계"), ("maps", "맵통계")]


def layout(cfg: Config, game: games.Game, active: str, body: str, title: str = "") -> str:
    opts = "".join(
        f'<option value="{g.key}"{" selected" if g.key == game.key else ""}{"" if g.ready else " disabled"}>'
        f'{e(g.title)}{"" if g.ready else " (준비 중)"}</option>' for g in games.GAMES)
    nav = "".join(
        f'<a href="/{game.key}/{p}"{" class=active" if p == active else ""}>{e(t)}</a>' for p, t in NAV)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title or cfg.site_name)} · {e(game.title)}</title>
<link rel="stylesheet" href="/static/app.css"></head><body>
<div class="topbar"><div class="topin">
<a href="/{game.key}/" class="brand">{e(cfg.site_name)}</a>
<select class="gamesel" aria-label="게임 선택" onchange="location.href='/'+this.value+'/{active}'">{opts}</select>
<nav class="nav">{nav}</nav></div></div>
<div class="wrap">{body}</div>
<div class="sitefoot">{e(cfg.site_name)} · RTS 전적 기록 · 리플레이는 각 게임 런처가 경기 후 자동으로 올립니다</div>
</body></html>"""


def race_badge(game_key: str, race) -> str:
    if game_key == "rakmu":
        if isinstance(race, int) and 0 <= race < len(rakmu.RACES):
            return (f'<span class="rbadge" style="background:{rakmu.RACE_COLORS[race]}" '
                    f'title="{e(rakmu.RACES[race])}">{e(rakmu.RACE_LETTERS[race])}</span>')
        return '<span class="rbadge rb-none" title="랜덤/기타">?</span>'
    return ""


def race_name(game_key: str, race) -> str:
    return rakmu.race_name(race) if game_key == "rakmu" else str(race)


def res_tag(r) -> str:
    return {"win": '<span class="res-w">W</span>', "lose": '<span class="res-l">L</span>',
            "draw": '<span class="res-d">D</span>'}.get(r, '<span class="res-u">-</span>')


def plink(game_key: str, name: str) -> str:
    return f'<a class="plink" href="/{game_key}/player/{q(name)}">{e(name)}</a>'


def pager(total: int, page: int, base: str, params: dict) -> str:
    pages = max(1, math.ceil(total / PER_PAGE))
    if pages <= 1:
        return ""

    def link(p, label=None, cls="pg"):
        qs = urllib.parse.urlencode({**{k: v for k, v in params.items() if v != ""}, "page": p})
        return f'<a class="{cls}" href="{base}?{qs}">{label or p}</a>'

    out = [link(page - 1, "‹") if page > 1 else '<span class="pg dis">‹</span>']
    for p in range(max(1, page - 3), min(pages, page + 3) + 1):
        out.append(f'<span class="pg on">{p}</span>' if p == page else link(p))
    out.append(link(page + 1, "›") if page < pages else '<span class="pg dis">›</span>')
    return f'<div class="pager">{"".join(out)}</div>'


def match_card(game_key: str, m: dict) -> str:
    ps = m["players"]
    if len(ps) == 2:
        a, b = ps
        if b.get("result") == "win" and a.get("result") != "win":
            a, b = b, a                    # 승자를 왼쪽에
        left = (f'<div class="mvside mvL {e(a.get("result") or "")}">{race_badge(game_key, a["race"])}'
                f'<span class="mvnm">{plink(game_key, a["name"])}</span>{res_tag(a.get("result"))}</div>')
        right = (f'<div class="mvside mvR {e(b.get("result") or "")}">{res_tag(b.get("result"))}'
                 f'<span class="mvnm">{plink(game_key, b["name"])}</span>{race_badge(game_key, b["race"])}</div>')
        vs = f'<div class="mvrow">{left}<div class="mvsep">vs</div>{right}</div>'
    else:
        items = "".join(
            f'<div class="mvside {e(p.get("result") or "")}">{race_badge(game_key, p["race"])}'
            f'<span class="mvnm">{plink(game_key, p["name"])}</span>{res_tag(p.get("result"))}</div>' for p in ps)
        vs = f'<div class="mvlist">{items}</div>'
    tag = '<span class="rktag rktag-rank">랭크게임</span>' if m["gametype"] == "rank" else \
        '<span class="rktag rktag-normal">일반게임</span>'
    src = {"server": "로비 서버 결과 (랭킹 반영)", "replay": "리플레이로 추정 (랭킹 미반영)"}.get(
        m.get("result_source"), "결과 미확인 (랭킹 미반영)")
    if m["replays"]:
        dl = (f'<a class="rbtn" href="/download/{e(m["hash"])}" title="리플레이 다운로드">리플레이 ⬇</a>'
              f'<span class="dlbox" title="다운로드 수">{m["downloads"]}</span>')
    else:
        dl = '<span class="rbtn rbtn-off" title="이 경기를 한 플레이어의 런처가 리플레이를 올리지 않았습니다">리플레이 없음</span>'
    dur = fmt_dur(m["duration"]) if m["duration"] else "시간 정보 없음"
    reps = "".join(
        f'<li><a href="/download/{e(m["hash"])}?r={r["id"]}">{e(r["filename"])}</a> '
        f'<span class="dim">{r["size"] // 1024}KB · {fmt_dur(r["duration"])} · 올린 사람 {e(r["uploader"] or "-")}</span></li>'
        for r in m["replays"])
    detail_players = "".join(
        f'<tr><td>{race_badge(game_key, p["race"])} {e(race_name(game_key, p["race"]))}</td><td>{plink(game_key, p["name"])}</td>'
        f'<td>{res_tag(p.get("result"))}</td><td class="dim">슬롯 {p["slot"] + 1}</td></tr>' for p in ps)
    played = m["played_at"][:16]
    img = rakmu.map_image(m["map_name"]) if game_key == "rakmu" else ""
    thumb = (f'<a class="mthumb" href="/{game_key}/maps#map={q(m["map_name"])}" title="맵 정보"><img src="{img}" alt="" loading="lazy"></a>'
             if img else "")
    return f"""<div class="mcard"><div class="mbody">
<div class="mmap">{thumb}<a class="mapnm" href="/{game_key}/matches?map={q(m['map_name'])}">{e(m['map_name'])}</a>
<div class="mapdur">{dur}</div></div>
<div class="mright">{vs}
<div class="mfoot"><span class="fdt">{e(played)}</span><div class="fbtns">{tag}{dl}</div></div></div></div>
<details class="mdet"><summary>상세보기</summary><div class="detin">
<table class="dtab"><tbody>{detail_players}</tbody></table>
<div class="dim">승패: {e(src)} · 경기 ID {e(m['hash'][:12])}</div>
<div class="rplist">리플레이 ({len(m['replays'])}개, 같은 경기를 여러 플레이어가 올리면 모두 보관)<ul>{reps}</ul></div>
</div></details></div>"""


# ---------------------------------------------------------------- 페이지
def page_matches(cfg, store: Store, game, params) -> str:
    page = int(params.get("page") or 1) if str(params.get("page") or "1").isdigit() else 1
    f = {k: params.get(k, "") for k in ("player", "race", "map", "gametype")}
    if f["race"] and not f["race"].isdigit():
        f["race"] = ""
    total, rows = store.match_rows(game.key, f["player"], f["race"], f["map"], f["gametype"], page, PER_PAGE)
    race_opts = "".join(f'<option value="{i}"{" selected" if f["race"] == str(i) else ""} style="color:{c}">■ {e(n)}</option>'
                        for i, (n, c) in enumerate(zip(rakmu.RACES, rakmu.RACE_COLORS)))
    map_opts = "".join(f'<option value="{e(mp["map_name"])}"{" selected" if f["map"] == mp["map_name"] else ""}>'
                       f'{e(mp["map_name"])} ({mp["n"]})</option>' for mp in store.maps(game.key))
    gt = f["gametype"]
    form = f"""<form class="msearch" method="get" action="/{game.key}/matches">
<div><label>플레이어 닉네임</label><input name="player" value="{e(f['player'])}" placeholder="닉네임" autocomplete="off"></div>
<div><label>종족</label><select name="race"><option value="">전체</option>{race_opts}</select></div>
<div><label>게임유형</label><select name="gametype"><option value="">전체</option>
<option value="rank"{" selected" if gt == "rank" else ""}>랭크게임</option>
<option value="normal"{" selected" if gt == "normal" else ""}>일반게임</option></select></div>
<div><label>맵</label><select name="map"><option value="">전체</option>{map_opts}</select></div>
<div class="fbtn"><button type="submit">검색</button><a class="reset" href="/{game.key}/matches">초기화</a></div></form>"""
    cards = "".join(match_card(game.key, m) for m in rows) or \
        '<div class="empty">아직 기록된 경기가 없습니다. 런처로 대전(사람 2명 이상) 리플레이가 올라오면 여기에 표시됩니다.</div>'
    body = (f'<div class="today">오늘 총 경기수: {store.today_count(game.key)} · 전체 {total}경기</div>{form}'
            f'{pager(total, page, f"/{game.key}/matches", f)}<div id="mlist">{cards}</div>'
            f'{pager(total, page, f"/{game.key}/matches", f)}')
    return layout(cfg, game, "matches", body, "전적기록")


def _rate(v) -> str:
    return "-" if v is None else f"{v:.1f}%"


def page_leaderboard(cfg, store, game, params) -> str:
    rows = store.leaderboard(game.key)
    trs = "".join(
        f'<tr><td class="rk">{r["rank"]}</td><td>{race_badge(game.key, r["main_race"])} {plink(game.key, r["name"])}</td>'
        f'<td class="num pts">{r["points"]}</td><td class="num">{r["w"]}</td><td class="num">{r["l"]}</td>'
        f'<td class="num">{r["d"]}</td><td class="num">{_rate(r["rate"])}</td>'
        f'<td class="dim">{e((r["last"] or "")[:16])}</td></tr>' for r in rows)
    body = f"""<h1>리더보드</h1><p class="dim">점수 = 1000 + 승×10 − 패×10 (라크무 게임 내 랭킹과 같은 규칙). 사람 2명 이상 경기만 집계합니다.</p>
<div class="tblwrap"><table class="tbl"><thead><tr><th>#</th><th>플레이어</th><th>점수</th><th>승</th><th>패</th><th>무</th><th>승률</th><th>최근 경기</th></tr></thead>
<tbody>{trs or '<tr><td colspan=8 class="empty">기록 없음</td></tr>'}</tbody></table></div>"""
    return layout(cfg, game, "", body, "리더보드")


def page_player(cfg, store, game, name, params) -> str:
    r = store.player(game.key, name)
    total, rows = store.match_rows(game.key, player="", page=1, per_page=10_000)
    mine = [m for m in rows if any(p["name"].lower() == name.lower() for p in m["players"])][:50]
    if r is None:
        head = f'<h1>{e(name)}</h1><div class="empty">랭킹에 들어간 경기(로비 서버 결과)가 아직 없습니다.</div>'
    else:
        rate = "-" if r["rate"] is None else f'{r["rate"]:.1f}%'
        head = (f'<h1>{race_badge(game.key, r["main_race"])} {e(r["name"])}</h1>'
                f'<div class="pstats"><div><b>{r["rank"]}</b>위</div><div><b>{r["points"]}</b>점</div>'
                f'<div><b>{r["w"]}</b>승 <b>{r["l"]}</b>패 <b>{r["d"]}</b>무</div><div>승률 <b>{rate}</b></div></div>')
    cards = "".join(match_card(game.key, m) for m in mine)
    return layout(cfg, game, "", head + f'<h2>최근 경기</h2><div id="mlist">{cards}</div>', name)


def page_stats(cfg, store, game, params) -> str:
    st = store.race_stats(game.key)
    rows = ""
    for i, n in enumerate(rakmu.RACES):
        p = st["per"].get(i) or {"n": 0, "w": 0, "l": 0}
        w, l = p["w"] or 0, p["l"] or 0
        rate = f"{100 * w / (w + l):.1f}%" if w + l else "-"
        rows += f'<tr><td>{race_badge(game.key, i)} {e(n)}</td><td class="num">{p["n"]}</td><td class="num">{w}</td><td class="num">{l}</td><td class="num">{rate}</td></tr>'
    head = "".join(f"<th>vs {race_badge(game.key, j)}</th>" for j in range(len(rakmu.RACES)))
    grid = ""
    for i in range(len(rakmu.RACES)):
        cells = ""
        for j in range(len(rakmu.RACES)):
            w, l = st["vs"].get((i, j), (0, 0))
            cells += f'<td class="num">{f"{100 * w / (w + l):.0f}% <span class=dim>({w}-{l})</span>" if w + l else "-"}</td>'
        grid += f"<tr><th>{race_badge(game.key, i)} {e(rakmu.RACES[i])}</th>{cells}</tr>"
    body = f"""<h1>게임통계</h1><h2>종족별 승률</h2><div class="tblwrap"><table class="tbl"><thead><tr><th>종족</th><th>출전</th><th>승</th><th>패</th><th>승률</th></tr></thead>
<tbody>{rows}</tbody></table></div><h2>종족 상성 (1:1, 행 종족 기준 승률)</h2>
<div class="tblwrap"><table class="tbl"><thead><tr><th></th>{head}</tr></thead><tbody>{grid}</tbody></table></div>"""
    return layout(cfg, game, "stats", body, "게임통계")


def _map_list(store, game) -> list[dict]:
    """맵 카드 목록: 경기가 있는 맵(많은 순) 다음에 아직 경기가 없는 맵(맵 목록 순)."""
    out, seen = [], set()
    for m in store.maps(game.key):
        out.append({**(rakmu.map_info(m["map_name"]) or {}), **m, "name": m["map_name"]})
        seen.add(m["map_name"])
    for c in rakmu.map_catalog():
        if c["name"] not in seen:
            out.append({**c, "n": 0, "avg_dur": None, "last": None, "ranked": 0})
    return out


def page_maps(cfg, store, game, params) -> str:
    maps = _map_list(store, game)
    counts = sorted({m["players"] for m in maps if m.get("players")})
    chips = '<button class="mxchip on" data-p="">전체</button>' + "".join(
        f'<button class="mxchip" data-p="{n}">{n}인</button>' for n in counts)
    cards = ""
    for m in maps:
        img = rakmu.map_image(m["name"])
        thumb = (f'<img class="mxthumb" src="{img}" alt="" loading="lazy">' if img
                 else '<div class="mxnoimg">미리보기 없음</div>')
        badges = (f'<span class="mxpl">{m["players"]}인</span>' if m.get("players") else "") + \
                 (f'<span class="mxrk">랭크 {m["ranked"]}</span>' if m.get("ranked") else "")
        if m["n"]:
            meta = f'<b>{m["n"]}</b>경기' + (f' · 평균 {fmt_dur(m["avg_dur"])}' if m.get("avg_dur") else "")
        else:
            meta = "0경기"
        if m.get("tileset"):
            meta += " · " + e(m["tileset"])
        cards += (f'<div class="mxcard{"" if m["n"] else " mxempty"}" data-nm="{e(m["name"])}" '
                  f'data-p="{m.get("players") or ""}" tabindex="0" role="button">'
                  f'<div class="mxthumbwrap">{thumb}{badges}</div>'
                  f'<div class="mxti"><div class="mxnm">{e(m["name"])}</div><div class="mxmeta">{meta}</div></div></div>')
    body = f"""<h1>맵통계</h1>
<p class="today">맵을 누르면 큰 미리보기와 그 맵의 종족 승률·상성, 많이 이긴 플레이어, 최근 경기를 볼 수 있습니다.
미리보기의 네모는 시작 위치(플레이어 색)입니다.</p>
<div class="mxchips">{chips}</div>
<div class="mxgrid">{cards or '<div class="empty">맵 정보 없음</div>'}</div>
<div id="mxmodal" class="mxmodal" hidden><button class="mxnav prev" title="이전 맵 (←)">‹</button>
<div class="mxbox" role="dialog" aria-modal="true"><div class="mxhd"><span id="mxtitle"></span>
<button class="mxx" title="닫기 (Esc)">✕</button></div><div class="mxbody" id="mxbody"></div></div>
<button class="mxnav next" title="다음 맵 (→)">›</button></div>
<script>{MAPS_JS.replace("__GAME__", game.key)}</script>"""
    return layout(cfg, game, "maps", body, "맵통계")


MAPS_JS = r"""
(function(){
var modal=document.getElementById('mxmodal'),body=document.getElementById('mxbody'),title=document.getElementById('mxtitle'),cur=null;
function cards(){return Array.prototype.filter.call(document.querySelectorAll('.mxcard'),function(c){return c.style.display!=='none';});}
function open(c){cur=c;var nm=c.getAttribute('data-nm');title.textContent=nm;modal.hidden=false;document.body.classList.add('mxlock');
 body.innerHTML='<div class="empty">불러오는 중…</div>';
 history.replaceState(null,'','#map='+encodeURIComponent(nm));
 fetch('/__GAME__/map/'+encodeURIComponent(nm)+'?frag=1').then(function(r){return r.text();})
  .then(function(t){if(cur===c)body.innerHTML=t;}).catch(function(){body.innerHTML='<div class="empty">불러오기 실패</div>';});}
function close(){modal.hidden=true;cur=null;document.body.classList.remove('mxlock');history.replaceState(null,'',location.pathname+location.search);}
function nav(d){var l=cards(),i=l.indexOf(cur);if(i<0)return;open(l[(i+d+l.length)%l.length]);}
var grid=document.querySelector('.mxgrid');
grid.addEventListener('click',function(ev){var c=ev.target.closest('.mxcard');if(c)open(c);});
grid.addEventListener('keydown',function(ev){var c=ev.target.closest('.mxcard');if(c&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();open(c);}});
modal.addEventListener('click',function(ev){if(ev.target===modal||ev.target.closest('.mxx'))close();else if(ev.target.closest('.mxnav.prev'))nav(-1);else if(ev.target.closest('.mxnav.next'))nav(1);});
document.addEventListener('keydown',function(ev){if(modal.hidden)return;if(ev.key==='Escape')close();else if(ev.key==='ArrowLeft')nav(-1);else if(ev.key==='ArrowRight')nav(1);});
document.querySelectorAll('.mxchip').forEach(function(b){b.addEventListener('click',function(){
 document.querySelectorAll('.mxchip').forEach(function(x){x.classList.toggle('on',x===b);});var p=b.getAttribute('data-p');
 document.querySelectorAll('.mxcard').forEach(function(c){c.style.display=(!p||c.getAttribute('data-p')===p)?'':'none';});});});
var h=decodeURIComponent((location.hash.match(/^#map=(.+)$/)||[])[1]||'');
if(h){var c=cards().filter(function(x){return x.getAttribute('data-nm')===h;})[0];if(c)open(c);}
})();
"""


def map_detail(store, game, name: str) -> str:
    """맵 하나의 미리보기 + 통계 (맵통계 창 안에 들어가는 조각, /<game>/map/<이름>?frag=1)."""
    info = rakmu.map_info(name) or {}
    st = store.map_stats(game.key, name)
    h = st["head"]
    img = rakmu.map_image(name)
    pic = f'<img class="mdimg" src="{img}" alt="{e(name)} 미리보기">' if img else '<div class="mxnoimg big">미리보기 없음</div>'
    facts = []
    if info:
        facts += [f'{info["players"]}인', e(info["tileset"]), f'{info["size"][0]} × {info["size"][1]}',
                  f'금광 {info["gold"]}개' + (f' (매장량 {info["gold_total"]:,})' if info.get("gold_total") else ""),
                  f'우물 {info["wells"]}개']
        if not info.get("official"):
            facts.append("추가 맵 (Maps/" + e(info["file"].split("/")[0]) + ")")
    tiles = "".join(f'<div class="mdtile"><div class="mdk">{k}</div><div class="mdv">{v}</div></div>' for k, v in (
        ("경기", h["n"] or 0), ("랭크게임", h["ranked"] or 0),
        ("평균 시간", fmt_dur(h["avg_dur"]) if h["avg_dur"] else "-"),
        ("최근 경기", e((h["last"] or "-")[:10]))))
    if not h["n"]:
        stats = '<div class="empty">이 맵에서 한 경기가 아직 없습니다.</div>'
    else:
        rows = ""
        total = sum((st["per"].get(i) or {}).get("n", 0) for i in range(len(rakmu.RACES))) or 1
        for i, rn in enumerate(rakmu.RACES):
            p = st["per"].get(i) or {"n": 0, "w": 0, "l": 0}
            w, l = p["w"] or 0, p["l"] or 0
            pick = 100 * p["n"] / total
            rate = f"{100 * w / (w + l):.0f}%" if w + l else "-"
            rows += (f'<tr><td>{race_badge(game.key, i)} {e(rn)}</td><td class="num">{p["n"]}</td>'
                     f'<td><div class="mdbar"><span style="width:{pick:.0f}%;background:{rakmu.RACE_COLORS[i]}"></span></div></td>'
                     f'<td class="num">{w}-{l}</td><td class="num">{rate}</td></tr>')
        unk = st["per"].get(None)
        if unk:
            rows += (f'<tr><td>{race_badge(game.key, None)} 모름</td><td class="num">{unk["n"]}</td>'
                     f'<td class="dim" colspan=3>리플레이가 없어 종족을 모르는 경기</td></tr>')
        head = "".join(f"<th>vs {race_badge(game.key, j)}</th>" for j in range(len(rakmu.RACES)))
        grid = ""
        for i in range(len(rakmu.RACES)):
            cells = ""
            for j in range(len(rakmu.RACES)):
                w, l = st["vs"].get((i, j), (0, 0))
                cells += f'<td class="num">{f"{100 * w / (w + l):.0f}% <span class=dim>({w}-{l})</span>" if w + l else "-"}</td>'
            grid += f"<tr><th>{race_badge(game.key, i)} {e(rakmu.RACES[i])}</th>{cells}</tr>"
        top = "".join(
            f'<tr><td>{plink(game.key, t["name"])}</td><td class="num">{t["n"]}</td>'
            f'<td class="num">{t["w"] or 0}-{t["l"] or 0}</td></tr>' for t in st["top"])
        recent = ""
        for m in st["recent"]:
            ps = " <span class=dim>vs</span> ".join(
                f'{race_badge(game.key, p["race"])} {plink(game.key, p["name"])} {res_tag(p.get("result"))}' for p in m["players"])
            dur = f' <span class="dim">· {fmt_dur(m["duration"])}</span>' if m["duration"] else ""
            recent += f'<li><span class="dim">{e(m["played_at"][:16])}</span> {ps}{dur}</li>'
        stats = f"""<h3>종족별 출전·승률</h3><div class="tblwrap"><table class="tbl"><thead><tr><th>종족</th><th>출전</th><th>선택 비율</th><th>승-패</th><th>승률</th></tr></thead><tbody>{rows}</tbody></table></div>
<h3>종족 상성 (1:1, 행 종족 기준 승률)</h3><div class="tblwrap"><table class="tbl"><thead><tr><th></th>{head}</tr></thead><tbody>{grid}</tbody></table></div>
<div class="mdcols"><div><h3>이 맵에서 많이 이긴 플레이어</h3><div class="tblwrap"><table class="tbl"><thead><tr><th>플레이어</th><th>경기</th><th>승-패</th></tr></thead><tbody>{top}</tbody></table></div></div>
<div><h3>최근 경기</h3><ul class="mdrecent">{recent}</ul>
<a class="rbtn" href="/{game.key}/matches?map={q(name)}">이 맵 전적 모두 보기 →</a></div></div>"""
    return f"""<div class="mdtop"><div class="mdpic">{pic}</div><div class="mdside">
<div class="mdfacts">{" · ".join(facts) if facts else '<span class="dim">맵 파일 정보 없음</span>'}</div>
<div class="mdtiles">{tiles}</div></div></div>{stats}"""


def page_map(cfg, store, game, name: str) -> str:
    body = (f'<p class="today"><a class="plink" href="/{game.key}/maps">← 맵통계</a></p><h1>{e(name)}</h1>'
            f'<div class="mxbox static"><div class="mxbody">{map_detail(store, game, name)}</div></div>')
    return layout(cfg, game, "maps", body, name)


def page_not_ready(cfg, game) -> str:
    return layout(cfg, game, "", f'<h1>{e(game.title)}</h1><div class="empty">이 게임의 전적 기록은 준비 중입니다.</div>')


# ---------------------------------------------------------------- 요청 처리
class Handler(BaseHTTPRequestHandler):
    server_version = "RtsArena/1.0"
    timeout = 30          # 느리거나 멈춘 연결이 스레드를 계속 붙잡지 않게
    cfg: Config
    store: Store

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def html(self, s: str, code: int = 200):
        self._send(code, s.encode("utf-8"), "text/html; charset=utf-8")

    def json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def redirect(self, to: str):
        self._send(302, b"", "text/plain", {"Location": to})

    def not_found(self):
        g = games.get(games.DEFAULT)
        self.html(layout(self.cfg, g, "", '<h1>404</h1><div class="empty">페이지를 찾을 수 없습니다.</div>'), 404)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._route_get()
        except Exception:  # noqa: BLE001
            log.exception("GET %s 실패", self.path)
            self._send(500, "서버 오류".encode("utf-8"), "text/plain; charset=utf-8")

    def _route_get(self):
        u = urllib.parse.urlsplit(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
        if not parts:
            return self.redirect(f"/{games.DEFAULT}/matches")
        if parts[0] == "healthz":
            # DB 가 실제로 응답해야 healthy (멈추면 Portainer 에서 unhealthy 로 보인다)
            try:
                self.store.db.one("SELECT 1")
            except Exception as e:  # noqa: BLE001
                return self.json({"ok": False, "error": str(e)}, 503)
            return self.json({"ok": True, "sync": SYNC_STATUS})
        if parts[0] == "static" and 2 <= len(parts) <= 4 and all(re.fullmatch(r"[\w-][\w.-]*", x) for x in parts[1:]):
            f = STATIC.joinpath(*parts[1:])
            if f.is_file():
                ctype = {"css": "text/css", "js": "text/javascript", "png": "image/png", "svg": "image/svg+xml"}.get(
                    f.suffix[1:], "application/octet-stream")
                return self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""),
                                  {"Cache-Control": "public, max-age=3600"})
            return self.not_found()
        if parts[0] == "download" and len(parts) == 2:
            rid = int(params["r"]) if params.get("r", "").isdigit() else None
            got = self.store.replay_for_download(parts[1], rid)
            if not got or not got[0].is_file():
                return self.not_found()
            path, fname = got
            disp = f"attachment; filename=\"replay{Path(fname).suffix}\"; filename*=UTF-8''{q(fname)}"
            return self._send(200, path.read_bytes(), "application/octet-stream", {"Content-Disposition": disp})
        if parts[0] == "api" and len(parts) == 3 and parts[2] == "matches.json":
            g = games.get(parts[1])
            if not g:
                return self.json({"error": "unknown game"}, 404)
            total, rows = self.store.match_rows(g.key, page=int(params.get("page", 1) or 1) if str(params.get("page", "1")).isdigit() else 1,
                                                per_page=50)
            return self.json({"total": total, "matches": rows})
        game = games.get(parts[0])
        if not game:
            return self.not_found()
        if not game.ready:
            return self.html(page_not_ready(self.cfg, game))
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "":
            return self.html(page_leaderboard(self.cfg, self.store, game, params))
        if sub == "matches":
            return self.html(page_matches(self.cfg, self.store, game, params))
        if sub == "stats":
            return self.html(page_stats(self.cfg, self.store, game, params))
        if sub == "maps":
            return self.html(page_maps(self.cfg, self.store, game, params))
        if sub == "map" and len(parts) == 3:
            if params.get("frag"):
                return self.html(map_detail(self.store, game, parts[2]))
            return self.html(page_map(self.cfg, self.store, game, parts[2]))
        if sub == "player" and len(parts) == 3:
            return self.html(page_player(self.cfg, self.store, game, parts[2], params))
        return self.not_found()

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0 or n > rakmu.MAX_REPLAY_BYTES:
            self.close_connection = True
            return self.json({"ok": False, "status": "rejected", "message": "파일 크기가 올바르지 않습니다"}, 413)
        # 본문을 먼저 다 읽는다: 읽지 않고 401 을 보내면 런처 쪽 전송이 끊겨(연결 재설정)
        # '토큰 오류' 대신 '네트워크 오류'로 보인다 (테스트에서 확인).
        data = self.rfile.read(n)
        if len(parts) != 3 or parts[0] != "api" or parts[2] != "replays":
            return self.json({"ok": False, "message": "not found"}, 404)
        if self.cfg.upload_token:
            got = self.headers.get("X-Arena-Token", "")
            if not hmac.compare_digest(got.encode(), self.cfg.upload_token.encode()):
                return self.json({"ok": False, "status": "rejected", "message": "업로드 토큰이 맞지 않습니다"}, 401)
        fname = urllib.parse.unquote(self.headers.get("X-Filename", ""))
        uploader = urllib.parse.unquote(self.headers.get("X-Uploader", ""))
        try:
            res = self.store.ingest(parts[1], data, fname, uploader)
        except Exception:  # noqa: BLE001
            log.exception("업로드 처리 실패")
            return self.json({"ok": False, "status": "error", "message": "서버 오류"}, 500)
        log.info("upload %s by %r: %s %s", fname, uploader, res.status, res.message)
        code = 200 if res.ok else 422
        return self.json({"ok": res.ok, "status": res.status, "message": res.message, "match": res.match_hash,
                          "url": f"/{parts[1]}/matches" if res.ok else ""}, code)


def make_server(cfg: Config) -> tuple[ThreadingHTTPServer, Store]:
    db = Database(cfg.db_path)
    src = RakmuServerSource(cfg.rakmu_matches_file, cfg.rakmu_matches_url)
    store = Store(db, cfg.replay_dir, src, cfg.match_window_minutes)
    handler = type("BoundHandler", (Handler,), {"cfg": cfg, "store": store})
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), handler)
    return httpd, store


def start_sync_thread(cfg: Config, store: Store) -> threading.Thread:
    stop = threading.Event()

    SYNC_STATUS.update(enabled=True, source=str(store.rakmu.file or store.rakmu.url))

    def sync_once():
        import datetime as _dt
        SYNC_STATUS["last_run"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            s = store.import_rakmu_records()
            SYNC_STATUS.update(error=store.rakmu.last_error, server_records=len(store.rakmu.records()),
                               created=s["created"], linked=s["linked"], results=s["results"],
                               mismatch=len(s["mismatch"]))
            if s["created"] or s["linked"] or s["results"]:
                log.info("로비 서버 동기화: 새 경기 %d, 리플레이 경기 연결 %d, 승패 반영 %d",
                         s["created"], s["linked"], s["results"])
            if s["mismatch"]:
                log.warning("게임 안 랭킹과 다른 계정 %d명: %s", len(s["mismatch"]), ", ".join(s["mismatch"][:20]))
        except Exception as e:  # noqa: BLE001
            SYNC_STATUS["error"] = f"{type(e).__name__}: {e}"
            log.exception("로비 서버 동기화 실패")

    def loop():
        sync_once()                       # 시작하자마자 한 번
        while not stop.wait(cfg.rakmu_sync_seconds):
            sync_once()

    t = threading.Thread(target=loop, name="rakmu-sync", daemon=True)
    t.stop = stop  # type: ignore[attr-defined]
    t.start()
    return t
