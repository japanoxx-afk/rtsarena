# 전적 사이트 NAS 배포 순서

라크무 서버(rakmu-live)를 올린 것과 같은 방식이다. 이미지 빌드·SSH 없이 File Station + Portainer 만 쓴다.
웹 사이트는 Python 표준 라이브러리만 써서 추가 설치가 없다.

## 0. 경로

| File Station | 컨테이너 안 | 내용 |
|---|---|---|
| `game-servers/rtsarena/app/arena/` | `/mnt/game-servers/rtsarena/app/arena` | 사이트 코드 |
| `game-servers/rtsarena/data/` | `/mnt/game-servers/rtsarena/data` | DB(arena.sqlite3), 리플레이(replays/), 로그 |
| `game-servers/rtsarena/backups/` | (사이트는 안 씀) | 사람이 백업 보관 |
| `game-servers/rakmu-live/data/matches.json` | 읽기만 | 라크무 로비 서버의 승패 기록 |

## 1. 준비 (내 PC)

```powershell
.\deploy\Prepare-NasUpload.ps1
```

`deploy\upload\rtsarena\` 아래에 올릴 파일이 모인다 (`app\arena\...`, `app\DEPLOYED_VERSION.txt`, `compose.yaml`, `NAS_DEPLOY.md`).
data 폴더는 없으므로 올려도 NAS 의 전적·리플레이가 덮어써지지 않는다.

## 2. File Station  (https://akansas.synology.me:5001/)

1. `game-servers` 를 연다. 다른 프로젝트 폴더(rakmu-live, rakmu-test 등)는 건드리지 않는다.
2. 새 폴더 `rtsarena` → 그 안에 `app`, `data`, `backups` 폴더를 만든다.
3. `rtsarena` 에 `compose.yaml`, `NAS_DEPLOY.md` 를 올린다.
4. `rtsarena/app` 에 **`arena` 폴더째로**, 그리고 `DEPLOYED_VERSION.txt` 를 올린다 ("업로드 - 덮어쓰기", 폴더 업로드).
   최종 모양:
   ```
   game-servers/rtsarena/compose.yaml
   game-servers/rtsarena/app/arena/__main__.py   ← 이 파일이 이 위치에 있어야 함
   game-servers/rtsarena/app/arena/static/app.css
   game-servers/rtsarena/app/arena/games/rakmu.py
   game-servers/rtsarena/data/                    (비어 있음 — 사이트가 채움)
   ```

## 3. Portainer  (https://akansas.synology.me:9443/)

1. 게임 서버 Docker 환경(rakmu-server 가 보이는 곳)을 고른다.
2. Containers 의 Published Ports 에서 **30013** 을 쓰는 컨테이너가 없는지 확인 (있으면 4번에서 WEB_PORT 를 다른 번호로).
3. Stacks → **+ Add stack** → Name: `rtsarena` → Build method: **Web editor** → `compose.yaml` 내용 전체 붙여 넣기.
4. 아래 **Environment variables** → + Add:
   - `ARENA_UPLOAD_TOKEN` = 직접 만든 업로드 키 (new-rakum GitHub Secret 과 **같은 값**)
   - (필요할 때만) `WEB_PORT` = 30013 대신 쓸 포트
5. **Deploy the stack**. 처음엔 python:3.12-slim 이미지를 받느라 1~2분.
6. Containers → `rtsarena-web` 이 running, 1분 뒤 healthy 인지 확인.
   Logs 에 `RTS ARENA on http://0.0.0.0:8080 (data=/mnt/game-servers/rtsarena/data, rakmu=/mnt/game-servers/rakmu-live/data/matches.json)` 이 보이면 정상.
7. File Station 에서 `rtsarena/data` 에 `arena.sqlite3`, `arena.log` 가 생겼는지 확인.

## 4. 확인 (내 PC, 외부에서)

- 브라우저 `http://akansas.synology.me:30013/healthz` → `{"ok": true}`
- `http://akansas.synology.me:30013/` → 전적 사이트 (처음엔 "아직 기록된 경기가 없습니다")
- 안 열리면: 공유기가 30013 을 VM 으로 전달하는지 서버 관리자에게 확인 요청 (VM 안에서는 되는데 외부에서 안 되는 경우).
- 런처 기본 주소는 `http://112.159.73.235:30013` (launcher.py `ARENA_URL`). 포트를 바꿨다면 런처도 바꿔야 한다.

## 5. 운영

- **코드 업데이트**: `rtsarena/app` 을 `backups/app-YYYYMMDD` 로 복사 → `Prepare-NasUpload.ps1` → `app/arena` 덮어쓰기 업로드
  → Portainer Containers → rtsarena-web → Restart. DB 는 data/ 에 있어 유지된다.
- **키 변경**: Stacks → rtsarena → Environment variables 수정 → Update the stack. 런처도 같은 키로 다시 빌드해야 한다.
- **백업**: `rtsarena/data` 를 `backups/data-YYYYMMDD` 로 복사 (arena.sqlite3 + replays/). 리플레이는 대전 1판 50~200KB.
- **로그**: Portainer Logs 또는 `rtsarena/data/arena.log` (업로드한 사람 이름·IP 가 있으니 공개하지 않는다).
