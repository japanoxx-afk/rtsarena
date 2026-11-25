"""HTTP 서버: 전적 페이지(서버에서 HTML 생성) + 런처 업로드 API + 리플레이 다운로드."""
from __future__ import annotations

import hashlib
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
from .launcher_info import LauncherInfo
from .rakmu_server import RakmuServerSource
from .store import Store

log = logging.getLogger("arena.web")
SYNC_STATUS: dict = {"enabled": False}   # /healthz 에 보여 줌 (NAS 로그를 못 볼 때 원인 확인용)
STATIC = Path(__file__).resolve().parent / "static"
PER_PAGE = 20
# 배포마다 바뀌는 CSS 주소 (브라우저가 예전 CSS 를 1시간 동안 쓰지 않게)
CSS_VER = hashlib.sha1((STATIC / "app.css").read_bytes()).hexdigest()[:10]


def e(s) -> str:
    return escape("" if s is None else str(s), quote=True)


def q(s) -> str:
    return urllib.parse.quote(str(s), safe="")


def fmt_dur(sec) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}분 {sec % 60}초"


# ---------------------------------------------------------------- 공통 틀
NAV = [("", "리더보드"), ("matches", "전적기록"), ("stats", "게임통계"), ("maps", "맵통계"), ("download", "다운로드")]


DISCORD_ICON = ('<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="'
                'M19.3 5.3A17 17 0 0 0 15 4l-.5 1.1a15.6 15.6 0 0 0-5 0L9 4a17 17 0 0 0-4.3 1.3C2 9.4 1.3 13.4 1.6 17.3'
                'A17.2 17.2 0 0 0 6.9 20l1.1-1.8c-.6-.2-1.2-.5-1.7-.9l.4-.3a12.2 12.2 0 0 0 10.6 0l.4.3c-.5.4-1.1.7-1.7.9'
                'l1.1 1.8a17.1 17.1 0 0 0 5.3-2.7c.4-4.5-.7-8.5-3.1-12zM8.7 14.9c-1 0-1.9-1-1.9-2.1s.8-2.1 1.9-2.1'
                ' 1.9.9 1.9 2.1-.8 2.1-1.9 2.1zm6.6 0c-1 0-1.9-1-1.9-2.1s.8-2.1 1.9-2.1 1.9.9 1.9 2.1-.8 2.1-1.9 2.1z"/></svg>')


def discord_link(cfg: Config) -> str:
    url = getattr(cfg, "discord_url", "")   # 설정 파일이 예전 것이어도 페이지는 뜨게
    if not re.fullmatch(r"https://(discord\.gg|discord\.com/invite)/[A-Za-z0-9-]{2,40}", url or ""):
        return ""
    return (f'<a class="discord" href="{e(url)}" target="_blank" rel="noopener" title="디스코드 참여">'
            f'{DISCORD_ICON}<span>디스코드</span></a>')


def layout(cfg: Config, game: games.Game, active: str, body: str, title: str = "") -> str:
    opts = "".join(
        f'<option value="{g.key}"{" selected" if g.key == game.key else ""}{"" if g.ready else " disabled"}>'
        f'{e(g.title)}{"" if g.ready else " (준비 중)"}</option>' for g in games.GAMES)
    nav = "".join(
        f'<a href="/{game.key}/{p}"{" class=active" if p == active else ""}>{e(t)}</a>' for p, t in NAV)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title or cfg.site_name)} · {e(game.title)}</title>
<link rel="stylesheet" href="/static/app.css?v={CSS_VER}"></head><body>
<div class="topbar"><div class="topin">
<a href="/{game.key}/" class="brand">{e(cfg.site_name)}</a>
<select class="gamesel" aria-label="게임 선택" onchange="location.href='/'+this.value+'/{active}'">{opts}</select>
<nav class="nav">{nav}</nav>{discord_link(cfg)}</div></div>
<div class="wrap">{body}</div>
<div class="sitefoot">{e(cfg.site_name)} · RTS 전적 기록 · 리플레이는 각 게임 런처가 경기 후 자동으로 올립니다</div>
</body></html>"""


def gmod(game_key: str):
    """게임별 해석 모듈 (RACES·맵 그림 등). 모듈이 없는 게임은 라크무 기본값."""
    g = games.get(game_key)
    return g.module if g and g.module else rakmu


def race_badge(game_key: str, race) -> str:
    mod = gmod(game_key)
    if not mod.RACES:
        return ""
    if isinstance(race, int) and 0 <= race < len(mod.RACES):
        return (f'<span class="rbadge" style="background:{mod.RACE_COLORS[race]}" '
                f'title="{e(mod.RACES[race])}">{e(mod.RACE_LETTERS[race])}</span>')
    return '<span class="rbadge rb-none" title="랜덤/기타">?</span>'


def race_name(game_key: str, race) -> str:
    return gmod(game_key).race_name(race)


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
    mod = gmod(game_key)
    if m["replays"]:
        dl = (f'<a class="rbtn" href="/download/{e(m["hash"])}" title="리플레이 다운로드">리플레이 ⬇</a>'
              f'<span class="dlbox" title="다운로드 수">{m["downloads"]}</span>')
    elif not getattr(mod, "HAS_REPLAYS", True):
        dl = '<span class="rbtn rbtn-off" title="이 게임은 아직 런처 리플레이 업로드를 지원하지 않습니다">리플레이 미지원</span>'
    else:
        dl = '<span class="rbtn rbtn-off" title="이 경기를 한 플레이어의 런처가 리플레이를 올리지 않았습니다">리플레이 없음</span>'
    dur = fmt_dur(m["duration"]) if m["duration"] else "시간 정보 없음"
    reps = "".join(
        f'<li><a href="/download/{e(m["hash"])}?r={r["id"]}">{e(r["filename"])}</a> '
        f'<span class="dim">{r["size"] // 1024}KB · {fmt_dur(r["duration"])} · 올린 사람 {e(r["uploader"] or "-")}</span></li>'
        for r in m["replays"])
    race_td = (lambda p: f'<td>{race_badge(game_key, p["race"])} {e(race_name(game_key, p["race"]))}</td>') if mod.RACES \
        else (lambda p: "")
    detail_players = "".join(
        f'<tr>{race_td(p)}<td>{plink(game_key, p["name"])}</td>'
        f'<td>{res_tag(p.get("result"))}</td><td class="dim">슬롯 {p["slot"] + 1}</td></tr>' for p in ps)
    played = m["played_at"][:16]
    rplist = (f'<div class="rplist">리플레이 ({len(m["replays"])}개, 같은 경기를 여러 플레이어가 올리면 모두 보관)<ul>{reps}</ul></div>'
              if getattr(mod, "HAS_REPLAYS", True) or m["replays"] else "")
    room = f' · 방 {e(m["room"])}' if m.get("room") else ""
    img = mod.map_image(m["map_name"])
    thumb = (f'<a class="mthumb" href="/{game_key}/maps#map={q(m["map_name"])}" title="맵 정보"><img src="{img}" alt="" loading="lazy"></a>'
             if img else "")
    return f"""<div class="mcard"><div class="mbody">
<div class="mmap">{thumb}<a class="mapnm" href="/{game_key}/matches?map={q(m['map_name'])}">{e(m['map_name'])}</a>
<div class="mapdur">{dur}</div></div>
<div class="mright">{vs}
<div class="mfoot"><span class="fdt">{e(played)}</span><div class="fbtns">{tag}{dl}</div></div></div></div>
<details class="mdet"><summary>상세보기</summary><div class="detin">
<table class="dtab"><tbody>{detail_players}</tbody></table>
<div class="dim">승패: {e(src)} · 경기 ID {e(m['hash'][:12])}{room}</div>
{rplist}
</div></details></div>"""


# ---------------------------------------------------------------- 페이지
def page_matches(cfg, store: Store, game, params) -> str:
    page = int(params.get("page") or 1) if str(params.get("page") or "1").isdigit() else 1
    f = {k: params.get(k, "") for k in ("player", "race", "map", "gametype")}
    if f["race"] and not f["race"].isdigit():
        f["race"] = ""
    total, rows = store.match_rows(game.key, f["player"], f["race"], f["map"], f["gametype"], page, PER_PAGE)
    mod = gmod(game.key)
    race_opts = "".join(f'<option value="{i}"{" selected" if f["race"] == str(i) else ""} style="color:{c}">■ {e(n)}</option>'
                        for i, (n, c) in enumerate(zip(mod.RACES, mod.RACE_COLORS)))
    race_field = (f'<div><label>종족</label><select name="race"><option value="">전체</option>{race_opts}</select></div>'
                  if mod.RACES else "")
    map_opts = "".join(f'<option value="{e(mp["map_name"])}"{" selected" if f["map"] == mp["map_name"] else ""}>'
                       f'{e(mp["map_name"])} ({mp["n"]})</option>' for mp in store.maps(game.key))
    gt = f["gametype"]
    form = f"""<form class="msearch" method="get" action="/{game.key}/matches">
<div><label>플레이어 닉네임</label><input name="player" value="{e(f['player'])}" placeholder="닉네임" autocomplete="off"></div>
{race_field}
<div><label>게임유형</label><select name="gametype"><option value="">전체</option>
<option value="rank"{" selected" if gt == "rank" else ""}>랭크게임</option>
<option value="normal"{" selected" if gt == "normal" else ""}>일반게임</option></select></div>
<div><label>맵</label><select name="map"><option value="">전체</option>{map_opts}</select></div>
<div class="fbtn"><button type="submit">검색</button><a class="reset" href="/{game.key}/matches">초기화</a></div></form>"""
    empty = ('아직 기록된 경기가 없습니다. 런처로 대전(사람 2명 이상) 리플레이가 올라오면 여기에 표시됩니다.'
             if getattr(mod, "HAS_REPLAYS", True) else
             '아직 기록된 경기가 없습니다. 인터넷 서버에서 대전(사람 2명 이상)을 마치면 1분 안에 여기에 표시됩니다.')
    cards = "".join(match_card(game.key, m) for m in rows) or f'<div class="empty">{e(empty)}</div>'
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
    note = getattr(gmod(game.key), "RULE_NOTE", "게임 내 랭킹과 같은 규칙")
    body = f"""<h1>리더보드</h1><p class="dim">점수 = 1000 + 승×10 − 패×10 ({e(note)}). 사람 2명 이상 경기만 집계합니다.</p>
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


def page_stats_basic(cfg, store, game) -> str:
    """종족 정보가 없는 게임: 경기·플레이어 수, 무승부 비율, 경기 많은 플레이어, 요일·시간대."""
    st = store.basic_stats(game.key)
    h = st["head"]
    top = "".join(
        f'<tr><td>{plink(game.key, r["name"])}</td><td class="num">{r["n"]}</td><td class="num">{r["w"] or 0}</td>'
        f'<td class="num">{r["l"] or 0}</td><td class="num">{r["d"] or 0}</td></tr>' for r in st["top"])
    hours = "".join(f'<td class="num">{st["hours"].get(f"{hh:02d}", 0)}</td>' for hh in range(0, 24, 1))
    hhead = "".join(f"<th>{hh}</th>" for hh in range(24))
    body = f"""<h1>게임통계</h1>
<div class="pstats"><div><b>{h["n"] or 0}</b>경기</div><div><b>{h["players"] or 0}</b>명 참가</div>
<div>무승부 <b>{h["draws"] or 0}</b>경기</div><div>결과 미확인 <b>{h["unknown"] or 0}</b>경기</div></div>
<h2>경기를 많이 한 플레이어</h2><div class="tblwrap"><table class="tbl"><thead><tr><th>플레이어</th><th>경기</th><th>승</th><th>패</th><th>무</th></tr></thead>
<tbody>{top or '<tr><td colspan=5 class="empty">기록 없음</td></tr>'}</tbody></table></div>
<h2>시간대별 경기 수</h2><div class="tblwrap"><table class="tbl"><thead><tr>{hhead}</tr></thead><tbody><tr>{hours}</tr></tbody></table></div>
<p class="dim">이 게임은 서버가 종족을 알 수 없어 종족별 승률·상성은 없습니다.</p>"""
    return layout(cfg, game, "stats", body, "게임통계")


def page_stats(cfg, store, game, params) -> str:
    mod = gmod(game.key)
    if not mod.RACES:
        return page_stats_basic(cfg, store, game)
    st = store.race_stats(game.key)
    rows = ""
    for i, n in enumerate(mod.RACES):
        p = st["per"].get(i) or {"n": 0, "w": 0, "l": 0}
        w, l = p["w"] or 0, p["l"] or 0
        rate = f"{100 * w / (w + l):.1f}%" if w + l else "-"
        rows += f'<tr><td>{race_badge(game.key, i)} {e(n)}</td><td class="num">{p["n"]}</td><td class="num">{w}</td><td class="num">{l}</td><td class="num">{rate}</td></tr>'
    head = "".join(f"<th>vs {race_badge(game.key, j)}</th>" for j in range(len(mod.RACES)))
    grid = ""
    for i in range(len(mod.RACES)):
        cells = ""
        for j in range(len(mod.RACES)):
            w, l = st["vs"].get((i, j), (0, 0))
            cells += f'<td class="num">{f"{100 * w / (w + l):.0f}% <span class=dim>({w}-{l})</span>" if w + l else "-"}</td>'
        grid += f"<tr><th>{race_badge(game.key, i)} {e(mod.RACES[i])}</th>{cells}</tr>"
    body = f"""<h1>게임통계</h1><h2>종족별 승률</h2><div class="tblwrap"><table class="tbl"><thead><tr><th>종족</th><th>출전</th><th>승</th><th>패</th><th>승률</th></tr></thead>
<tbody>{rows}</tbody></table></div><h2>종족 상성 (1:1, 행 종족 기준 승률)</h2>
<div class="tblwrap"><table class="tbl"><thead><tr><th></th>{head}</tr></thead><tbody>{grid}</tbody></table></div>"""
    return layout(cfg, game, "stats", body, "게임통계")


def _map_list(store, game) -> list[dict]:
    """맵 카드 목록: 경기가 있는 맵(많은 순) 다음에 아직 경기가 없는 맵(맵 목록 순)."""
    mod = gmod(game.key)
    out, seen = [], set()
    for m in store.maps(game.key):
        out.append({**(mod.map_info(m["map_name"]) or {}), **m, "name": m["map_name"]})
        seen.add(m["map_name"])
    for c in mod.map_catalog():
        if c["name"] not in seen:
            out.append({**c, "n": 0, "avg_dur": None, "last": None, "ranked": 0})
    return out


def page_maps(cfg, store, game, params) -> str:
    cards = ""
    mod = gmod(game.key)
    for m in _map_list(store, game):
        img = mod.map_image(m["name"])
        thumb = f'<img src="{img}" alt="" loading="lazy">' if img else '<span class="mxnoimg">미리보기 없음</span>'
        badge = '<span class="mxrkbadge">랭크맵</span>' if m.get("ranked") else ""
        cnt = f'<b>{m["n"]}</b>경기' if m["n"] else "0경기"
        cards += (f'<div class="mxcard{"" if m["n"] else " mxempty"}" data-nm="{e(m["name"])}" tabindex="0" role="button">'
                  f'<div class="mxthumbwrap">{thumb}{badge}</div>'
                  f'<div class="mxti"><div class="mxnm" title="{e(m["name"])}">{e(m["name"])}</div>'
                  f'<div class="mxmeta">{cnt}</div></div></div>')
    body = f"""<div class="mxgrid">{cards or '<div class="empty">맵 정보 없음</div>'}</div>
<div id="mxmodal" class="mxmodal" hidden><button class="mxnav prev" title="이전 맵 (←)">‹</button>
<div class="mxbox" role="dialog" aria-modal="true"><div class="mxhd"><span id="mxtitle"></span>
<button class="mxx" title="닫기 (Esc)">✕</button></div><div class="mxbody" id="mxbody"></div></div>
<button class="mxnav next" title="다음 맵 (→)">›</button></div>
<script>{MAP_STATS_JS}{MAPS_JS.replace("__GAME__", game.key)}</script>"""
    return layout(cfg, game, "maps", body, "맵통계")


MAPS_JS = r"""
(function(){
var modal=document.getElementById('mxmodal'),body=document.getElementById('mxbody'),title=document.getElementById('mxtitle'),cur=null;
function cards(){return Array.prototype.slice.call(document.querySelectorAll('.mxcard'));}
function open(c){cur=c;var nm=c.getAttribute('data-nm');title.textContent=nm;modal.hidden=false;document.body.classList.add('mxlock');
 body.innerHTML='<div class="empty">불러오는 중…</div>';
 history.replaceState(null,'','#map='+encodeURIComponent(nm));
 fetch('/__GAME__/map/'+encodeURIComponent(nm)+'?frag=1').then(function(r){return r.text();})
  .then(function(t){if(cur!==c)return;body.innerHTML=t;body.scrollTop=0;mstatsInit(body);})
  .catch(function(){body.innerHTML='<div class="empty">불러오기 실패</div>';});}
function close(){modal.hidden=true;cur=null;document.body.classList.remove('mxlock');history.replaceState(null,'',location.pathname+location.search);}
function nav(d){var l=cards(),i=l.indexOf(cur);if(i<0)return;open(l[(i+d+l.length)%l.length]);}
var grid=document.querySelector('.mxgrid');
grid.addEventListener('click',function(ev){var c=ev.target.closest('.mxcard');if(c)open(c);});
grid.addEventListener('keydown',function(ev){var c=ev.target.closest('.mxcard');if(c&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();open(c);}});
modal.addEventListener('click',function(ev){if(ev.target===modal||ev.target.closest('.mxx'))close();else if(ev.target.closest('.mxnav.prev'))nav(-1);else if(ev.target.closest('.mxnav.next'))nav(1);});
document.addEventListener('keydown',function(ev){if(modal.hidden)return;if(ev.key==='Escape')close();
 else if(ev.target.tagName!=='SELECT'&&ev.key==='ArrowLeft')nav(-1);else if(ev.target.tagName!=='SELECT'&&ev.key==='ArrowRight')nav(1);});
var h=decodeURIComponent((location.hash.match(/^#map=(.+)$/)||[])[1]||'');
if(h){var c=cards().filter(function(x){return x.getAttribute('data-nm')===h;})[0];if(c)open(c);}
})();
"""

# 통계 지표: 맵 조각 안의 JSON(참가자별 원자료)을 종족·상대 종족으로 걸러 승리 시/패배 시/전체 평균을 낸다.
MAP_STATS_JS = r"""
function mstatsInit(root){
 root.querySelectorAll('.mstats').forEach(function(m){
  if(m.__init)return;m.__init=1;
  var data=JSON.parse(m.querySelector('.mstats-data').textContent);
  var st={race:'all',opp:'all',view:'table'};
  function fmt(k,v){if(v==null)return '-';if(k==='dur'){v=Math.round(v);return Math.floor(v/60)+':'+('0'+v%60).slice(-2);}
   return v.toLocaleString('ko-KR',{maximumFractionDigits:1});}
  function avg(rows,k){var s=0,n=0;rows.forEach(function(r){var v=r[k];if(v!=null&&!(k==='dur'&&!v)){s+=v;n++;}});return n?s/n:null;}
  function draw(){
   var rows=data.entries.filter(function(r){return (st.race==='all'||String(r.race)===st.race)&&(st.opp==='all'||String(r.opp)===st.opp);});
   var groups={win:rows.filter(function(r){return r.result==='win';}),lose:rows.filter(function(r){return r.result==='lose';}),all:rows};
   var out='';
   if(st.view==='table'){
    out='<table class="mstbl"><thead><tr><th></th><th class="cw">승리 시</th><th class="cl">패배 시</th><th class="ca">전체</th></tr></thead><tbody>';
    data.metrics.forEach(function(mt){out+='<tr><th>'+mt[1]+'</th>'+['win','lose','all'].map(function(g){
      return '<td'+(g==='all'?' class="ca"':'')+'>'+fmt(mt[0],avg(groups[g],mt[0]))+'</td>';}).join('')+'</tr>';});
    out+='</tbody></table>';
   }else{
    out='<div class="msgraph">';
    data.metrics.forEach(function(mt){var v={win:avg(groups.win,mt[0]),lose:avg(groups.lose,mt[0]),all:avg(groups.all,mt[0])};
     var mx=Math.max(v.win||0,v.lose||0,v.all||0)||1;
     out+='<div class="msg"><div class="msgt">'+mt[1]+'</div>'+[['win','승리 시'],['lose','패배 시'],['all','전체']].map(function(g){
      return '<div class="msgr"><span class="msgl '+g[0]+'">'+g[1]+'</span><div class="msgb"><span class="'+g[0]+'" style="width:'+(100*(v[g[0]]||0)/mx)+'%"></span></div><span class="msgv">'+fmt(mt[0],v[g[0]])+'</span></div>';}).join('')+'</div>';});
    out+='</div>';
   }
   var nrep=rows.filter(function(r){return r.peak_army!=null;}).length;
   out+='<div class="msnote">대상 '+rows.length+'명 (승 '+groups.win.length+' · 패 '+groups.lose.length+')'+
     ' · 병력·유닛·건물은 리플레이가 있는 '+nrep+'명 기준'+(st.opp!=='all'?' · 상대 종족은 1:1 경기만':'')+'</div>';
   m.querySelector('.mstats-out').innerHTML=out;
  }
  m.addEventListener('click',function(ev){
   var b=ev.target.closest('[data-race]');if(b){st.race=b.getAttribute('data-race');
    m.querySelectorAll('[data-race]').forEach(function(x){x.classList.toggle('on',x===b);});draw();return;}
   var v=ev.target.closest('[data-view]');if(v){st.view=v.getAttribute('data-view');
    m.querySelectorAll('[data-view]').forEach(function(x){x.classList.toggle('on',x===v);});draw();}});
  m.querySelector('.msopp').addEventListener('change',function(ev){st.opp=ev.target.value;draw();});
  draw();
 });
}
"""

METRICS = [("dur", "평균 게임시간"), ("peak_army", "평균 최대 병력"), ("units_made", "평균 유닛 수 (누적)"),
           ("buildings_made", "평균 건물 수 (누적)")]


def _rate_cell(w: int, l: int) -> tuple[str, str]:
    """(배경 스타일, 글자) — 50% 보다 높으면 초록, 낮으면 빨강, 차이가 클수록 진하게."""
    rate = 100 * w / (w + l)
    a = min(abs(rate - 50) / 50, 1) * 0.55 + 0.12
    bg = (f"rgba(46,160,80,{a:.2f})" if rate > 50 else f"rgba(200,55,55,{a:.2f})" if rate < 50
          else "rgba(120,125,140,.22)")
    return f"background:{bg}", f'<b>{rate:.1f}%</b><span>{w}승 {l}패</span>'


def map_detail(store, game, name: str) -> str:
    """맵 하나의 통계 (맵통계 창 안에 들어가는 조각, /<game>/map/<이름>?frag=1)."""
    mod = gmod(game.key)
    info = mod.map_info(name) or {}
    st = store.map_stats(game.key, name)
    h = st["head"]
    races = range(len(mod.RACES))
    img = mod.map_image(name)
    pic = f'<img src="{img}" alt="{e(name)} 미리보기">' if img else '<span class="mxnoimg">미리보기 없음</span>'
    facts = ""
    if info:
        facts = " · ".join([f'{info["players"]}인', e(info["tileset"]), f'{info["size"][0]}×{info["size"][1]}',
                            f'금광 {info["gold"]}', f'우물 {info["wells"]}'])
        if not info.get("official"):
            facts += " · 추가 맵"

    # ---- 종족별 승률 (행 = 내 종족, 열 = 상대 종족)
    head_cells = "".join(f'<th class="mrh">{race_badge(game.key, j)}</th>' for j in races)
    body_rows = ""
    for i in races:
        p = st["per"].get(i) or {"n": 0, "w": 0, "l": 0, "d": 0}
        w, l, n = p["w"] or 0, p["l"] or 0, p["n"] or 0
        if w + l:
            rate = 100 * w / (w + l)
            cls = "up" if rate > 50 else "down" if rate < 50 else ""
            tot = f'<b class="{cls}">{rate:.1f}%</b>'
        else:
            tot = '<b class="dim">-</b>'
        cells = ""
        for j in races:
            if i == j:
                mir = st["mirror"].get(i, 0)
                cells += f'<td class="mrc mir" title="같은 종족 경기 수">{mir or "–"}</td>'
                continue
            cw, cl = st["vs"].get((i, j), (0, 0))
            if cw + cl:
                style, txt = _rate_cell(cw, cl)
                cells += f'<td class="mrc" style="{style}">{txt}</td>'
            else:
                cells += '<td class="mrc none">–</td>'
        body_rows += (f'<tr><th class="mrr">{race_badge(game.key, i)}</th>'
                      f'<td class="mrt">{tot}<span>{n}전 {w}승 {l}패</span></td>{cells}</tr>')
    unk = (st["per"].get(None) or {}).get("n")
    unk_note = f'<div class="msnote">종족을 모르는 참가 {unk}명은 빠져 있습니다 (리플레이가 없는 경기).</div>' if unk else ""
    matrix = f"""<table class="mrtbl"><thead><tr><th colspan="2" class="mrtot">총 진행게임 수<b>{h["n"] or 0}전</b></th>{head_cells}</tr></thead>
<tbody>{body_rows}</tbody></table>{unk_note}"""
    if not mod.RACES:
        matrix = f'<div class="pstats"><div><b>{h["n"] or 0}</b>경기</div></div>'
    sec1 = f"""<section class="msec"><h3>{"종족별 승률" if mod.RACES else "경기 수"}</h3><div class="mrwrap">
<div class="mrpic"><div class="mrimg">{pic}</div><div class="mrfacts">{facts}</div></div>
<div class="mrright">{matrix}</div></div></section>"""

    # ---- 통계 지표
    data = {"metrics": METRICS, "entries": st["entries"]}
    rbtns = '<button class="msrb on" data-race="all">전체</button>' + "".join(
        f'<button class="msrb" data-race="{i}"><span style="background:{mod.RACE_COLORS[i]}">{e(mod.RACES[i])}</span></button>'
        for i in races)
    opps = '<option value="all">전체</option>' + "".join(f'<option value="{i}">{e(mod.RACES[i])}</option>' for i in races)
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    sec2 = f"""<section class="msec mstats"><h3>통계 지표</h3>
<div class="msbar"><div class="msraces">{rbtns}</div><div class="msright"><label>상대 <select class="msopp">{opps}</select></label>
<div class="msview"><button class="on" data-view="table">표</button><button data-view="graph">그래프</button></div></div></div>
<div class="mstats-out"></div><script type="application/json" class="mstats-data">{blob}</script></section>"""

    # ---- 최근 경기 · 많이 이긴 플레이어
    recent = ""
    for m in st["recent"]:
        ps = '<span class="dim"> vs </span>'.join(
            f'{race_badge(game.key, p["race"])} {plink(game.key, p["name"])} {res_tag(p.get("result"))}' for p in m["players"])
        dur = fmt_dur(m["duration"]) if m["duration"] else ""
        recent += f'<tr><td class="dim">{e(m["played_at"][5:16])}</td><td>{ps}</td><td class="dim num">{dur}</td></tr>'
    top = "".join(
        f'<tr><td>{plink(game.key, t["name"])}</td><td class="num">{t["n"]}전</td>'
        f'<td class="num">{t["w"] or 0}승 {t["l"] or 0}패</td></tr>' for t in st["top"][:5])
    sec3 = ""
    if h["n"]:
        sec3 = f"""<section class="msec"><h3>최근 경기</h3><div class="mdcols">
<table class="mstbl small"><tbody>{recent}</tbody></table>
<table class="mstbl small"><thead><tr><th colspan="3">이 맵에서 많이 이긴 플레이어</th></tr></thead><tbody>{top}</tbody></table></div>
<a class="rbtn mdmore" href="/{game.key}/matches?map={q(name)}">이 맵 전적 모두 보기 →</a></section>"""
    return sec1 + sec2 + sec3


def page_map(cfg, store, game, name: str) -> str:
    body = (f'<p class="today"><a class="plink" href="/{game.key}/maps#map={q(name)}">← 맵통계</a></p><h1>{e(name)}</h1>'
            f'<div class="mxbody static">{map_detail(store, game, name)}</div>'
            f'<script>{MAP_STATS_JS}mstatsInit(document);</script>')
    return layout(cfg, game, "maps", body, name)


def _fmt_size(n) -> str:
    return f"{n / 1048576:.1f} MB" if n else ""


def page_download(cfg, game, launcher: dict | None = None) -> str:
    sections = ""
    if game.key == "rakmu" and cfg.rakmu_launcher_version_url:
        if launcher:
            meta = " · ".join(x for x in (f"v{launcher['version']}", _fmt_size(launcher.get("size"))) if x)
            sha = (f'<div class="dlurl">SHA256 <code>{e(launcher["sha256"])}</code></div>'
                   if launcher.get("sha256") else "")
            sections += f"""<div class="dlbox"><h2 class="dlh">최신 런처</h2>
<a class="dlbtn" href="/{game.key}/download/launcher">⬇ 최신 런처 받기 <span class="dlver">{e(meta)}</span></a>{sha}
<ul class="dlnote">
<li>항상 GitHub 의 최신 버전으로 연결됩니다. 이미 설치했다면 런처를 그냥 켜기만 해도 자동으로 업데이트됩니다.</li>
<li>이 주소를 공유해도 늘 최신 런처가 받아집니다: <code>/{game.key}/download/launcher</code></li>
</ul></div>"""
        else:
            sections += ('<div class="dlbox"><h2 class="dlh">최신 런처</h2>'
                         '<div class="empty">최신 런처 정보를 확인하는 중입니다. 잠시 뒤 새로고침하세요.</div></div>')
    if game.key == "samchun":
        rel = "https://github.com/japanoxx-afk/samchun/releases"
        sections += f"""<div class="dlbox"><h2 class="dlh">최신 런처</h2>
<a class="dlbtn" href="{rel}" target="_blank" rel="noopener">⬇ 삼국지천명2 런처 (GitHub 릴리스)</a>
<ul class="dlnote">
<li>맨 위 버전의 <code>launcher.exe</code> 를 받습니다. 이미 설치했다면 런처를 켜기만 해도 자동으로 업데이트됩니다.</li>
<li>런처 1.3.58 부터 인터넷 대전 리플레이가 이 사이트에 자동으로 올라옵니다.</li>
</ul></div>"""
    url = cfg.install_url(game.key)
    if url and urllib.parse.urlsplit(url).scheme in ("http", "https"):
        btn = (f'<a class="dlbtn" href="{e(url)}" target="_blank" rel="noopener">⬇ {e(game.title)} 설치파일 받기</a>'
               f'<div class="dlurl">{e(url)}</div>')
    else:
        btn = '<div class="empty">설치파일 링크를 준비 중입니다.</div>'
    sections += f"""<div class="dlbox"><h2 class="dlh">게임 설치파일</h2>{btn}
<ul class="dlnote">
<li>링크를 누르면 NAS 공유 페이지가 새 창으로 열립니다. 거기서 파일을 골라 다운로드하세요.</li>
<li>사람끼리 한 대전은 경기가 끝나면 런처가 리플레이를 이 사이트에 자동으로 올립니다.</li>
</ul></div>"""
    return layout(cfg, game, "download", f"<h1>{e(game.title)} 다운로드</h1>{sections}", title="다운로드")


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
        if sub == "download" and len(parts) == 3 and parts[2] == "launcher" and game.key == "rakmu":
            info = self.launcher.get()
            if not info:
                return self.not_found()
            return self._send(302, b"", "text/plain", {"Location": info["url"], "Cache-Control": "no-store"})
        if sub == "download" and len(parts) == 2:
            return self.html(page_download(self.cfg, game, self.launcher.get() if game.key == "rakmu" else None))
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
        limit = getattr(gmod(parts[1]) if len(parts) == 3 else rakmu, "MAX_REPLAY_BYTES", rakmu.MAX_REPLAY_BYTES)
        if n <= 0 or n > limit:
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
    srcs = {k: RakmuServerSource(f, u) for k, (f, u) in cfg.lobby_sources().items()}
    store = Store(db, cfg.replay_dir, srcs.pop("rakmu"), cfg.match_window_minutes, srcs)
    launcher = LauncherInfo(cfg.rakmu_launcher_version_url, cfg.data_dir / "launcher_latest.json")
    launcher.get()   # 시작하자마자 뒤에서 한 번 받아 둔다
    handler = type("BoundHandler", (Handler,), {"cfg": cfg, "store": store, "launcher": launcher})
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), handler)
    return httpd, store


def start_sync_thread(cfg: Config, store: Store) -> threading.Thread:
    """설정된 모든 게임의 로비(호환) 서버 전적을 RAKMU_SYNC_SECONDS 마다 가져온다."""
    stop = threading.Event()
    keys = [k for k in store.sources if store.source(k)]
    SYNC_STATUS.update(enabled=True)
    for k in keys:
        src = store.sources[k]
        SYNC_STATUS[k] = {"source": str(src.file or src.url)}

    def sync_once(key):
        import datetime as _dt
        st = SYNC_STATUS[key]
        st["last_run"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        src = store.sources[key]
        try:
            s = store.import_server_records(key)
            st.update(error=src.last_error, server_records=len(src.records()),
                      created=s["created"], linked=s["linked"], results=s["results"],
                      playbacks_removed=st.get("playbacks_removed", 0) + s.get("playbacks_removed", 0),
                      mismatch=len(s["mismatch"]))
            if s["created"] or s["linked"] or s["results"]:
                log.info("[%s] 로비 서버 동기화: 새 경기 %d, 리플레이 경기 연결 %d, 승패 반영 %d",
                         key, s["created"], s["linked"], s["results"])
            if s.get("playbacks_removed"):
                log.info("다시보기 녹화 리플레이 %d개 정리", s["playbacks_removed"])
            if s["mismatch"]:
                log.warning("[%s] 게임 안 랭킹과 다른 계정 %d명: %s", key, len(s["mismatch"]), ", ".join(s["mismatch"][:20]))
        except Exception as e:  # noqa: BLE001
            st["error"] = f"{type(e).__name__}: {e}"
            log.exception("[%s] 로비 서버 동기화 실패", key)

    def loop():
        while True:
            for k in keys:                # 시작하자마자 한 번, 그다음 주기마다
                sync_once(k)
            if stop.wait(cfg.rakmu_sync_seconds):
                return

    t = threading.Thread(target=loop, name="lobby-sync", daemon=True)
    t.stop = stop  # type: ignore[attr-defined]
    t.start()
    return t
