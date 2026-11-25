# HANDOFF — RTS ARENA 전적 사이트 (다음 작업자 / 다른 PC / 다른 AI 용)

최종 갱신: 2026-11-26
비밀번호·토큰은 여기에 적지 않는다.

## 1. 목표 (사용자 요구사항 원문 요약)

1. RTS 전적 사이트. 먼저 **라크무**. 참고: http://jw2-arena.com/matches
2. 상단 **드롭박스로 타이틀(게임)을 바꾸면** 다른 RTS 전적을 본다.
3. 경기마다 **리플레이 다운로드** (필수).
4. 리플레이는 **런처가 업로드** → NAS 서버에 저장.
5. 라크무는 싱글플레이도 리플레이가 저장된다 → **나 + 컴퓨터만 있고 상대 플레이어가 없으면 전적·리플레이를 저장하지 않는다.**
6. 라크무 서버 쪽에 맵·진영 등 수정이 필요하면 알려 줄 것 → `docs/RAKMU_SERVER_CHANGES.md`
7. 다른 컴퓨터에서도 이어서 작업할 수 있게 이 문서를 유지한다.

## 2. 상태

| 항목 | 상태 |
|---|---|
| 웹 사이트 (리더보드·전적기록·게임통계·맵통계·플레이어·다운로드) | 구현, PC 에서 실행·화면 확인 |
| 게임 드롭박스 (라크무 / 임팩트 오브 파워=준비 중) | 구현 |
| 업로드 API `POST /api/rakmu/replays` + 싱글 거절 | 구현, 테스트 통과 |
| 런처 자동 업로드 모듈 `launcher/rakmu/arena_upload.py` | 구현, 테스트 통과 |
| 실제 리플레이 4개로 판정 확인 (C:\Users\seo\Downloads\replays) | 싱글 3개 skip, 대전 1개 upload — 맞음 |
| new-rakum 런처에 연결 | **v0.9063, 브랜치 `launcher-v0.9063-arena-upload` 푸시함 (main 미병합)** — v0.9061(NAS 기본 서버) + main 의 v0.9062(벽 브러시) + 업로드. 작업 폴더 `C:\Users\seo\Documents\claud\rhakmu-arena` (git worktree). CI 테스트 목록 93개 통과, 런처 15초 실행 확인. GitHub Secret ARENA_UPLOAD_TOKEN 등록됨(사용자) |
| NAS 배포 (Portainer 스택) | **완료 2026-11-21** — http://akansas.synology.me:30013/ , 컨테이너 rtsarena-web healthy |
| 라크무 서버/기록기 수정 | **안 함** — `docs/RAKMU_SERVER_CHANGES.md` (상대 이름 누락이 제일 중요) |
| NAS 코드 업데이트 | **손으로** (File Station 덮어쓰기 + 재시작). 자동 배포는 계획만 — 5-1 |
| 사이트 랭킹 = 게임 안 랭킹 | 2026-11-22 수정(98fe222): 다시보기 녹화 중복 제거 + 결과코드 판정. **NAS 반영 필요** |
| **삼국지천명2 (드롭박스)** | 2026-11-26 구현·테스트 (21개 통과). 호환 서버 1.3.58(samchun 저장소)의 `data/matches.json` 을 1분마다 가져옴. 리더보드 = 게임 안 랭킹 (실제 서버 코드로 60판 만들어 대조, 완전 일치). **NAS: samchun 스택 배포 + rtsarena 코드·compose 업데이트 필요** |
| 참고 사이트 방문 | HTTPS 는 연결 거부, `curl http://jw2-arena.com/matches` 는 됨 (구조 참고용) |

## 3. 구조

```
arena/
  __main__.py      python -m arena  (로그: data/arena.log, 1MB×3)
  config.py        환경변수 → 설정 (아래 표)
  db.py            SQLite 스키마: matches / match_players / replays
  store.py         업로드 접수(ingest), 같은 경기 합치기, 로비 서버 결과 동기화, 페이지 조회
  rakmu_server.py  라크무 로비 서버 matches.json 읽기 (파일 또는 URL)
  web.py           HTTP 라우팅 + HTML 생성 (템플릿 엔진 없음, 서버에서 바로 만듦)
  games/__init__.py  게임 목록(드롭박스). 새 게임 = 모듈 추가 + ready=True
  games/rakmu.py   .rkr 해석: 맵 이름, 종족(위국군/아스트리아/바르바로이), 사람/컴퓨터, 경기 시간, 승패 추정
  static/app.css
launcher/rakmu/arena_upload.py   라크무 런처용 (new-rakum 으로 복사해서 씀)
deploy/compose.yaml              NAS Portainer 스택
tests/test_arena.py              단위 + HTTP 종단 시험 (10개)
tools/demo_seed.py               가짜 경기 30개
tools/render_map_previews.py     맵 미리보기 PNG + 맵 목록 만들기 (게임 설치 PC, Tk 사용)
.claude/launch.json              Claude Code 미리보기용 (data-demo 로 실행)
```

설계 결정:
- **표준 라이브러리만** (http.server, sqlite3). 이유: NAS 는 Portainer 웹만 쓸 수 있고 SSH 가 없어서
  라크무 서버처럼 `python:3.12-slim` 이미지가 볼륨의 코드를 바로 실행하는 방식이 제일 단순하다 (pip 설치·이미지 빌드 없음).
- 이 PC 에는 Node 가 없다. Python 3.12.10 만 있음.
- 같은 경기를 두 플레이어가 각자 올리면: 같은 맵 + 시간 ±15분 + 같은 시드 + 겹치는 이름 → **한 경기에 리플레이 여러 개**로 보관.
  기본 다운로드는 가장 긴 리플레이, 상세보기에서 각각 받을 수 있다.
- **랭킹 동기화 (2026-11-21)**: 기준은 라크무 로비 서버 `matches.json` 하나.
  - 사이트는 60초마다(시작 때 한 번 더) 서버 기록의 모든 대전(사람 2명 이상)을 경기로 가져온다. 리플레이가 없으면 "리플레이 없음".
    리플레이가 나중에 오면 server_ref(서버 0.9017 의 경기 id, 옛 기록은 `시간|참가자`)로 같은 경기에 붙는다.
  - 리더보드 = 서버 결과(`result_source='server'`)만, 계정 이름 그대로(대소문자 구분), 점수 1000 + 승×10 − 패×10,
    정렬 (점수↓, 승↓, 패↑, 이름) — server.py `compute_ranking`/`ranking_rows` 와 같다.
  - 리플레이로 추정한 승패(마지막 장면 건물)는 경기 카드에만 보이고 랭킹에는 안 들어간다.
  - 동기화 때마다 서버 방식으로 센 값과 리더보드를 비교해 다르면 arena.log 에 "게임 안 랭킹과 다른 계정" 경고.
  - 서버 0.9017 부터 matches.json 을 전부 보관(500판 제한 삭제)하므로 두 랭킹이 계속 같다. 0.9016 이전에 잘린 기록은 복구 불가.
- 랭크/일반: 로비 서버 결과코드에 0x80 이 있으면 랭크게임.

## 3-1. 삼국지천명2 (2026-11-26)

- 기준: 호환 서버(`japanoxx-afk/samchun` `server/compat_server.py` 1.3.58)가 결과를 받을 때마다 쓰는
  `game-servers/samchun-live/data/matches.json` (라크무 matches.json 과 같은 모양 → `rakmu_server.ServerRecord` 로 그대로 읽음).
  `{"id","time","room","map","players":[계정],"results":{계정:{"code":1|2|3,"at"}}}`. 코드 4(짧은 경기)는 서버가 내보내지 않는다.
- players = 게임 중계(4901)에 들어온 계정. 1명이면 사이트가 가져오지 않음(혼자 만든 방).
- 게임 안 랭킹(대기실 랭킹 메뉴)도 서버 1.3.58 에서 같은 규칙(1000+승×10−패×10, 점수→승→패→이름)으로 구현 → 사이트와 같음.
- 종족 없음(서버가 모름), 맵은 방 정보에서 `.map` 이름을 찾을 때만. 리플레이 업로드 없음(경기 카드에 "리플레이 미지원").
  → `arena/games/samchun.py` 의 RACES 가 비어 있으면 화면이 종족 필터·종족 통계를 숨기고 게임통계는 기본 통계(`Store.basic_stats`)를 보여 준다.
- 설정: `SAMCHUN_MATCHES_FILE` (compose 기본값 `/mnt/game-servers/samchun-live/data/matches.json`) 또는 `SAMCHUN_MATCHES_URL`
  (호환 서버 `--http-port 4902` 의 `/matches.json`).
- 동기화는 게임별 (`Store.import_server_records(game)`), `/healthz` 의 `sync.<게임>` 에 상태.
- 새 게임 추가 방법: `arena/games/<key>.py` (TITLE, RACES…, map_display, map_image, HAS_REPLAYS, RULE_NOTE) + `games.GAMES` +
  `Config.lobby_sources()`.

## 4. 라크무 리플레이(.rkr) 핵심 사실

- gzip JSON 줄. 1줄 머리: `date`(시작 시각, 플레이어 PC 시간), `map`(`DATA\MAPS\[2]Distant.rkm`), `players`[{index,name,race}],
  `races`[8], `slots`[8], `seed`. 나머지 줄: 장면 `t`(초), `k`/`c`/`x` 유닛.
- `slots`: **0x20=사람, 0x10=컴퓨터, 0=빈 자리** (싱글 3개 [32,16], 대전 1개 [32,32] 로 확인).
- 종족 번호: 0 무휼, 1 아렌, 2 마르두크, 3 = 빈 자리/기타. **화면 이름(2026-11-23 사용자 지정)**: 위국군 / 아스트리아 / 바르바로이
  (`arena/games/rakmu.py` `RACES`·`RACE_LETTERS`). DB `match_players.race` 는 번호라 기존 기록은 고칠 것이 없고, 코드만 바꾸면 모든 화면이 바뀐다.
  게임 방 화면·런처도 new-rakum v0.9092 에서 같은 이름으로 바꿈.
- **알려진 문제**: 대전 리플레이에서 상대 이름이 빈 문자열. 사이트가 로비 서버 기록으로 채우고, 못 채우면 거절한다.
- 원본 코드: new-rakum `replay_recorder.py`, `replay_memory.py`, `replay_model.py`.

## 5. NAS 배포 순서 (완료, 코드 업데이트 때도 같은 순서) — 클릭 순서는 deploy/NAS_DEPLOY.md, 올릴 파일은 deploy/Prepare-NasUpload.ps1

1. File Station: `game-servers/rtsarena/app/` 만들고 이 저장소의 `arena/` 폴더를 통째로 올린다
   (`__pycache__` 제외) → `game-servers/rtsarena/app/arena/__main__.py` 가 있어야 함. `game-servers/rtsarena/data/` 도 만든다.
2. Portainer > Stacks > Add stack `rtsarena` > Web editor 에 `deploy/compose.yaml` 붙여 넣기.
3. Environment variables: `ARENA_UPLOAD_TOKEN` = (긴 임의 문자열, 런처와 동일), 필요하면 `WEB_PORT`.
4. Deploy → `http://<VM>:30013/healthz` 가 `{"ok": true}`.
5. 외부 `http://112.159.73.235:30013` 확인 (공유기 30000~30999 → VM 전달이라고 new-rakum `nas-server/ports.txt` 에 기록됨).
6. 업데이트: app/arena 파일 교체 → 컨테이너 재시작. DB 는 data/ 에 있어 유지된다 (스키마는 `CREATE IF NOT EXISTS`,
   나중에 더한 열은 db.py `ADDED_COLUMNS` 로 열 때 자동 추가). **현재는 코드가 바뀔 때마다 사용자가 손으로 한다** → 5-1 참고.

### 5-1. 자동 배포 (계획, 사용자 승인 대기 — 2026-11-22 제안)

문제: 사이트 코드를 고칠 때마다 사용자가 File Station 에 `deploy/upload/rtsarena/app/arena` 를 덮어쓰고
Portainer 에서 `rtsarena-web` 을 재시작해야 한다 (수정이 잦아 매번 부탁하게 됨). 사용자 질문: "매번 이렇게 해야 해?"

이미 자동인 것 (손 댈 필요 없음): 런처 리플레이 업로드, 1분마다 로비 서버 동기화(승패·랭킹), 다시보기 녹화 정리,
플레이어 지표 채우기, DB 열 추가 — 모두 사이트가 시작할 때/동기화할 때 스스로 한다.

제안한 방법 (rtsarena 저장소는 공개라 토큰 없이 받을 수 있음 — `https://codeload.github.com/japanoxx-afk/rtsarena/zip/refs/heads/<브랜치>` 200 확인):
1. 작은 시작 파일(예: `deploy/bootstrap.py`, 표준 라이브러리만)을 NAS `rtsarena/app/` 에 한 번 올리고,
   Portainer 스택 command 를 `python -u /mnt/game-servers/rtsarena/app/bootstrap.py` 로 한 번 바꾼다.
2. bootstrap: 시작할 때 GitHub 에서 **`live` 브랜치** zip 을 받아 `app/arena` 를 교체(임시 폴더에 풀고 검사 후 바꿔치기) → `python -m arena` 실행.
   받기 실패·압축 손상이면 기존 `app/arena` 로 그대로 켠다 (GitHub 장애에도 사이트는 켜짐).
3. 10분마다 `live` 의 커밋이 바뀌었는지 확인 → 바뀌면 프로세스 종료 → `restart: unless-stopped` 로 재시작되며 새 코드 적용.
   (api.github.com 비인증 한도 60회/시간 → 10분 간격이면 충분. 또는 zip ETag 비교)
4. `main` 이 아니라 `live` 브랜치만 따라간다: 테스트(`python -m unittest tests.test_arena`)가 통과한 커밋만 `live` 로 올린다
   → main 에 실수가 들어가도 실서버에 바로 나가지 않는다.
5. 토큰·DB·리플레이는 지금처럼 NAS `data/` 와 Portainer 환경변수에만 있다. GitHub 에서 받는 것은 코드뿐.
   compose 는 `read_only: true` 지만 app/ 는 볼륨이라 쓸 수 있다 (지금도 data/ 에 쓴다).
6. 적용 뒤 확인: `/healthz` 에 배포된 커밋(예: `"version": "<sha>"`)을 보여 주면 반영 여부를 밖에서 알 수 있다.

라크무 로비 서버(`rakmu-live/app`, new-rakum 저장소)도 같은 방식이 가능하지만, 게임 서버라 따로 신중히 진행할 것
(경기 중 재시작되면 안 됨 → 방이 없을 때만 재시작하는 조건 필요).

환경변수:

| 이름 | 기본 | 설명 |
|---|---|---|
| ARENA_DATA_DIR | `./data` | DB·리플레이·로그 |
| ARENA_HOST / ARENA_PORT | 0.0.0.0 / 8080 | |
| ARENA_UPLOAD_TOKEN | (빈값=토큰 없이 받음) | 런처 헤더 `X-Arena-Token` |
| RAKMU_MATCHES_FILE | | 로비 서버 matches.json 경로 (NAS 는 `/mnt/game-servers/rakmu-live/data/matches.json`) |
| RAKMU_MATCHES_URL | | 파일 대신 `http://서버:11225/matches.json` |
| RAKMU_SYNC_SECONDS | 60 | 결과 동기화 주기 |
| ARENA_MATCH_WINDOW_MINUTES | 15 | 같은 경기로 볼 시간 차 |
| ARENA_SITE_NAME | RTS ARENA | |

## 6. API

- `POST /api/<game>/replays` 본문 = .rkr 원본 바이트. 헤더 `X-Arena-Token`, `X-Filename`(URL 인코딩), `X-Uploader`.
  응답 `{ok, status: created|merged|duplicate|rejected, message, match}`. 거절은 422, 토큰 오류 401.
- `GET /download/<경기hash>[?r=<리플레이id>]` 리플레이 내려받기 (다운로드 수 +1)
- `GET /api/<game>/matches.json?page=` , `GET /healthz`
- 페이지: `/<game>/` 리더보드, `/<game>/matches?player=&race=&map=&gametype=&page=`, `/<game>/stats`, `/<game>/maps`, `/<game>/player/<이름>`

## 7. 다른 PC 에서 이어 하기

```powershell
git clone https://github.com/japanoxx-afk/rtsarena.git
```

```powershell
cd rtsarena; python -m unittest discover -s tests -v
```

```powershell
python tools/demo_seed.py; $env:ARENA_DATA_DIR="data-demo"; python -m arena
```

관련 저장소: 라크무 런처·서버 `https://github.com/japanoxx-afk/new-rakum` (NAS 배포 문서: `nas-server/HANDOFF.txt`),
임팩트 오브 파워 `https://github.com/japanoxx-afk/iop`.

## 8. 다음 할 일 (순서)

1. `launcher-v0.9063-arena-upload` 의 CI exe 로 실제 대전 1판 → 사이트에 올라오는지 확인 → main 병합(= 사용자 자동 업데이트).
   주의: main 에는 v0.9061(NAS 기본 서버)이 없었다. 이 브랜치를 병합하면 v0.9061 도 함께 배포된다.
   arena_upload.py 를 고치면 rtsarena `launcher/rakmu/` 와 new-rakum 루트 두 곳을 똑같이 맞춘다.
2. ~~NAS 에 rtsarena 스택 배포~~ (2026-11-21 완료).
3. 리플레이 기록기에서 상대 이름 채우기, 결과코드 기록 (docs/RAKMU_SERVER_CHANGES.md A-1, A-2).
4. ~~로비 서버 경기 ID·500판 제한·원자적 저장~~ (서버 0.9017, 브랜치 launcher-v0.9063-arena-upload). NAS rakmu-live/app 반영 필요.
5. ~~맵 미리보기·맵통계~~ (2026-11-21 완료). 새 맵이 생기면 게임이 설치된 PC 에서
   `python tools/render_map_previews.py --rakum <new-rakum 폴더>` → `arena/static/maps/rakmu/`, `arena/games/rakmu_maps.json` 커밋 후 NAS 에 올린다.
   목록에 없는 맵(다른 사람이 만든 맵)은 카드에 '미리보기 없음'으로 나온다.
6. **NAS 자동 배포 (5-1)** — 사용자가 진행하라고 하면: `deploy/bootstrap.py` + compose command 변경 + `live` 브랜치 만들기
   + `/healthz` 에 커밋 표시 + deploy/NAS_DEPLOY.md 에 한 번만 하는 전환 순서. 그 전까지는 코드 바꿀 때마다 사용자에게
   `deploy/Prepare-NasUpload.ps1` 결과 폴더 덮어쓰기 + 재시작을 부탁한다.
7. 임팩트 오브 파워: iop 저장소 `REPLAY_RESEARCH.md` 확인 후 `arena/games/iop.py` 추가.
8. 공지사항·대회 페이지 (참고 사이트에 있음) — 필요하면.

## 9. 작업 기록

- 2026-11-21: 저장소 생성, 사이트·업로드 API·런처 모듈·테스트·문서 작성. 실제 리플레이 4개로 판정 확인.
- 2026-11-21: new-rakum 런처에 자동 업로드 연결. 처음 v0.9062 로 만들었으나 main 이 이미 다른 v0.9062(벽 브러시)를
  배포해서 main 을 병합하고 v0.9063 으로 올림 (브랜치 launcher-v0.9063-arena-upload, 커밋 62d1c94 푸시).
  올린 사람 = 리플레이 머리의 내 플레이어(local) 이름.
- 2026-11-21: NAS 배포 완료. 스택 rtsarena / 컨테이너 rtsarena-web healthy. 외부 확인:
  http://akansas.synology.me:30013/ (= 112.159.73.235:30013) healthz 200, 전적 페이지 200, 토큰 없는 업로드 401.
  배포 중 겪은 문제: ① File Station 폴더 업로드로 arena 위치가 어긋남 → app/arena/__main__.py 위치 확인이 핵심.
  ② NAS 볼륨에서 SQLite WAL 이 'database is locked' → 일반 저널 + 잠금 실패 시 nolock 으로 고침(db.py).
- 2026-11-21: 맵통계를 카드 그리드 + 맵별 상세 창(큰 미리보기, 종족 출전·승률, 상성, 많이 이긴 플레이어, 최근 경기)으로 바꿈.
  미리보기는 런처 map_preview.render_tk 와 같은 실제 타일 그림 + 시작 위치. 전적 카드에도 작은 맵 그림.
- 2026-11-22: 사이트 랭킹이 게임 안 랭킹과 달랐던 원인 2가지 수정.
  (1) 런처가 '게임 화면으로 재생'(다시보기)도 녹화해 올려서 같은 경기가 여러 개 생김 (같은 시드 617586081 이 19번 등).
      같은 시드의 첫 녹화보다 3분 넘게 늦게 시작한 리플레이 = 다시보기 → 업로드는 duplicate 로 받고 저장 안 함,
      이미 저장된 것은 동기화할 때 리플레이 단위로 지우고 경기(맵·시드·시간)를 바로잡음 (Store.cleanup_playbacks).
      new-rakum main 의 런처는 recorder.pause_for_playback 으로 다시보기를 녹화하지 않는다(그 전 버전 사용자 대비).
  (2) matches.json 의 "wl" 은 예전 서버가 점수로 잘못 매긴 값일 수 있음 → 서버 랭킹처럼 결과코드로 다시 판정
      (rakmu_server.server_results = server.py _recompute_winloss 와 같은 규칙).
  실제 리플레이 115개 + 실제 matches.json 으로 재현: 경기 17개 → 7개, 리더보드 = 게임 안 랭킹 (완전 일치).
- 2026-11-22: 사용자가 "매번 이렇게(File Station 덮어쓰기+재시작) 해야 해?" → GitHub `live` 브랜치를 따라가는 자동 배포를 제안, 5-1 에 기록 (승인 대기, 아직 구현 안 함).
- 2026-11-26: 삼국지천명2 추가 (드롭박스 선택 시 모든 메뉴·전적이 삼국지천명2 로 바뀜). samchun 저장소 서버 1.3.58 에
  게임 안 랭킹 + matches.json 내보내기 + NAS 배포(deploy/NAS_DEPLOY.md, 스택 `samchun`, 포트 4800/4901/4902) 를 함께 넣음.
  rtsarena NAS 반영: app/arena 덮어쓰기 + 스택 compose 를 이 저장소 deploy/compose.yaml 로 Update (SAMCHUN_MATCHES_FILE 추가).
