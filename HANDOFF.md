# HANDOFF — RTS ARENA 전적 사이트 (다음 작업자 / 다른 PC / 다른 AI 용)

최종 갱신: 2026-11-21
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
| new-rakum 런처에 연결 | **완료, 로컬 커밋만 (푸시 안 함)** — new-rakum 브랜치 `launcher-v0.9062-arena-upload` 커밋 7effd80, 작업 폴더 `C:\Users\seo\Documents\claud\rhakmu-arena` (git worktree). 런처 15초 실행 확인, 테스트 41개 통과 |
| NAS 배포 (Portainer 스택) | **안 함** — `deploy/compose.yaml` + 아래 5장 |
| 라크무 서버/기록기 수정 | **안 함** — `docs/RAKMU_SERVER_CHANGES.md` (상대 이름 누락이 제일 중요) |
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
  games/rakmu.py   .rkr 해석: 맵 이름, 종족(무휼/아렌/마르두크), 사람/컴퓨터, 경기 시간, 승패 추정
  static/app.css
launcher/rakmu/arena_upload.py   라크무 런처용 (new-rakum 으로 복사해서 씀)
deploy/compose.yaml              NAS Portainer 스택
tests/test_arena.py              단위 + HTTP 종단 시험 (10개)
tools/demo_seed.py               가짜 경기 30개
.claude/launch.json              Claude Code 미리보기용 (data-demo 로 실행)
```

설계 결정:
- **표준 라이브러리만** (http.server, sqlite3). 이유: NAS 는 Portainer 웹만 쓸 수 있고 SSH 가 없어서
  라크무 서버처럼 `python:3.12-slim` 이미지가 볼륨의 코드를 바로 실행하는 방식이 제일 단순하다 (pip 설치·이미지 빌드 없음).
- 이 PC 에는 Node 가 없다. Python 3.12.10 만 있음.
- 같은 경기를 두 플레이어가 각자 올리면: 같은 맵 + 시간 ±15분 + 같은 시드 + 겹치는 이름 → **한 경기에 리플레이 여러 개**로 보관.
  기본 다운로드는 가장 긴 리플레이, 상세보기에서 각각 받을 수 있다.
- 승패 출처 우선순위: ① 로비 서버 matches.json 결과코드 (`server`) ② 리플레이 마지막 장면에 건물이 남은 사람 1명 (`replay`, 추정) ③ 모름.
  사이트가 60초마다 로비 서버 기록을 다시 읽어 ①로 덮어쓴다 (최근 14일).
- 점수 = 1000 + 승×10 − 패×10 (라크무 서버 게임 내 랭킹과 같은 규칙, server.py `ranking_rows`).
- 랭크/일반: 로비 서버 결과코드에 0x80 이 있으면 랭크게임.

## 4. 라크무 리플레이(.rkr) 핵심 사실

- gzip JSON 줄. 1줄 머리: `date`(시작 시각, 플레이어 PC 시간), `map`(`DATA\MAPS\[2]Distant.rkm`), `players`[{index,name,race}],
  `races`[8], `slots`[8], `seed`. 나머지 줄: 장면 `t`(초), `k`/`c`/`x` 유닛.
- `slots`: **0x20=사람, 0x10=컴퓨터, 0=빈 자리** (싱글 3개 [32,16], 대전 1개 [32,32] 로 확인).
- 종족 번호: 0 무휼, 1 아렌, 2 마르두크, 3 = 빈 자리/기타.
- **알려진 문제**: 대전 리플레이에서 상대 이름이 빈 문자열. 사이트가 로비 서버 기록으로 채우고, 못 채우면 거절한다.
- 원본 코드: new-rakum `replay_recorder.py`, `replay_memory.py`, `replay_model.py`.

## 5. NAS 배포 순서 (아직 안 함) — 클릭 순서는 deploy/NAS_DEPLOY.md, 올릴 파일은 deploy/Prepare-NasUpload.ps1

1. File Station: `game-servers/rtsarena/app/` 만들고 이 저장소의 `arena/` 폴더를 통째로 올린다
   (`__pycache__` 제외) → `game-servers/rtsarena/app/arena/__main__.py` 가 있어야 함. `game-servers/rtsarena/data/` 도 만든다.
2. Portainer > Stacks > Add stack `rtsarena` > Web editor 에 `deploy/compose.yaml` 붙여 넣기.
3. Environment variables: `ARENA_UPLOAD_TOKEN` = (긴 임의 문자열, 런처와 동일), 필요하면 `WEB_PORT`.
4. Deploy → `http://<VM>:30013/healthz` 가 `{"ok": true}`.
5. 외부 `http://112.159.73.235:30013` 확인 (공유기 30000~30999 → VM 전달이라고 new-rakum `nas-server/ports.txt` 에 기록됨).
6. 업데이트: app/arena 파일 교체 → 컨테이너 재시작. DB 는 data/ 에 있어 유지된다 (스키마는 `CREATE IF NOT EXISTS`).

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

1. new-rakum 에 GitHub Secret `ARENA_UPLOAD_TOKEN` 추가 → `launcher-v0.9062-arena-upload` 브랜치 푸시(CI 가 exe 빌드) → main 병합.
   arena_upload.py 를 고치면 rtsarena `launcher/rakmu/` 와 new-rakum 루트 두 곳을 똑같이 맞춘다.
2. NAS 에 rtsarena 스택 배포 (5장), 외부 포트 확인.
3. 리플레이 기록기에서 상대 이름 채우기, 결과코드 기록 (docs/RAKMU_SERVER_CHANGES.md A-1, A-2).
4. 로비 서버 경기 ID (B-1).
5. 맵 미리보기 이미지: 리플레이 머리 `map_rkm`(base64 맵 원본)으로 new-rakum `map_preview.py` 를 써서 썸네일 생성.
6. 임팩트 오브 파워: iop 저장소 `REPLAY_RESEARCH.md` 확인 후 `arena/games/iop.py` 추가.
7. 공지사항·대회 페이지 (참고 사이트에 있음) — 필요하면.

## 9. 작업 기록

- 2026-11-21: 저장소 생성, 사이트·업로드 API·런처 모듈·테스트·문서 작성. 실제 리플레이 4개로 판정 확인.
- 2026-11-21: new-rakum 런처 v0.9062 에 자동 업로드 연결 (브랜치 launcher-v0.9062-arena-upload, 미푸시).
  올린 사람 = 리플레이 머리의 내 플레이어(local) 이름.
