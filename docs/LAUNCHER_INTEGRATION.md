# 라크무 런처에 자동 업로드 붙이기

대상 저장소: `japanoxx-afk/new-rakum` (라크무 런처 `launcher.py`, PyInstaller 로 exe 빌드)
**아직 new-rakum 에는 반영하지 않았다.** 아래 순서대로 넣으면 된다.

## 1. 파일 복사

`rtsarena/launcher/rakmu/arena_upload.py` → `new-rakum/arena_upload.py` (launcher.py 옆)

- 표준 라이브러리만 쓰므로 추가 pip 설치 없음.
- `.github/workflows/build-launcher.yml` 의 `paths:` 에 `'arena_upload.py'` 추가 (안 하면 이 파일만 바뀌었을 때 exe 가 안 만들어짐).
- 런처 `.spec` 은 `launcher.py` 에서 import 하면 PyInstaller 가 자동으로 포함한다.

## 2. launcher.py 수정

```python
import arena_upload

ARENA_URL = "http://112.159.73.235:30013"   # NAS 의 rtsarena-web (deploy/compose.yaml 의 WEB_PORT)
ARENA_TOKEN = ""                           # 업로드 토큰 — 저장소에 평문으로 두지 말고 빌드 때 넣는다(아래 3)
```

`self.recorder = replay_recorder.Recorder(...)` 바로 아래(현재 약 727줄):

```python
        self.arena = arena_upload.ArenaUploader(
            self.base_dir, replay_recorder.replay_dir(self.base_dir),
            self.cfg.get("arena_url", ARENA_URL), ARENA_TOKEN,
            log_fn=lambda m: None)
        if self.cfg.get("arena_upload", True):
            self.arena.start()
```

`self.recorder.stop()` 을 부르는 곳(종료 처리, 약 1202·1952줄) 옆에 `self.arena.stop()`.

선택(권장):
- 리플레이 탭에 체크박스 "전적 사이트에 자동 업로드" (`cfg["arena_upload"]`), 상태 글자 `self.arena.status` 표시.
- 리플레이 목록에 열 하나 추가해 `self.arena.file_status(path)` ("업로드됨 / 싱글(안 올림) / 거절됨") 표시.
- 버튼 "전적 사이트 열기" → `webbrowser.open(ARENA_URL + "/rakmu/matches")`.

## 3. 토큰

- NAS Portainer 스택 환경변수 `ARENA_UPLOAD_TOKEN` 과 런처의 `ARENA_TOKEN` 이 같아야 한다.
- 저장소에 평문으로 넣지 말 것. 방법: GitHub → new-rakum → Settings → Secrets → `ARENA_UPLOAD_TOKEN` 추가,
  빌드 단계 전에 `launcher.py` 의 `ARENA_TOKEN = ""` 를 시크릿 값으로 바꿔 쓰는 한 줄을 workflow 에 넣는다.
- exe 에서 뽑아낼 수는 있으므로 "아무나 못 올리게" 정도의 장치다. 서버는 그래서 리플레이 내용을 직접 검사한다
  (사람 2명 이상, 로비 서버 기록과 대조).

## 4. 동작 규칙 (arena_upload.py)

| 상황 | 동작 |
|---|---|
| 나 + 컴퓨터만 (slots 에 0x20 이 1개) | 올리지 않음, `skipped` 로 기록 (다시 안 봄) |
| 사람 2명 이상 | 업로드 |
| 기록 중(gzip 미완성)이거나 수정 15초 이내 | 다음 주기(30초)에 다시 확인 |
| 서버 연결 실패 | 다음 주기에 재시도 |
| 서버 거절(422) | `rejected` 기록, 다시 안 올림 |
| 토큰 오류(401) | 상태에 표시, 기록 안 함 (토큰 고친 뒤 자동 재시도) |

상태 파일: `<런처 폴더>/arena_upload.json`. 지우면 모든 리플레이를 다시 검사·업로드한다(서버는 sha256 로 중복 무시).

## 5. 수동 시험

```powershell
python launcher/rakmu/arena_upload.py "C:\경로\replays" http://127.0.0.1:8080
```
