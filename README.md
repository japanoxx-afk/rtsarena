# rtsarena

RTS 게임 전적·리플레이 사이트. **라크무(RhakMu)** 와 **삼국지천명2** 가 동작하고, 상단 드롭박스로 게임을 고르면
리더보드·전적기록·게임통계·맵통계 메뉴와 전적이 모두 그 게임 것으로 바뀐다 (임팩트 오브 파워는 "준비 중").

- 참고한 사이트: http://jw2-arena.com/matches (리더보드 / 전적기록 / 게임통계 / 맵통계, 경기 카드 + 리플레이 다운로드)
- **다음 작업자는 [HANDOFF.md](HANDOFF.md) 부터 읽는다.** NAS 배포는 [deploy/NAS_DEPLOY.md](deploy/NAS_DEPLOY.md).

## 구성

| 경로 | 내용 |
|---|---|
| `arena/` | 웹 서버 (Python 3.12 표준 라이브러리만, SQLite) — `python -m arena` |
| `arena/games/` | 게임별 해석기. `rakmu.py` = 라크무 `.rkr` 리플레이 해석, `samchun.py` = 삼국지천명2 (호환 서버 전적만) |
| `launcher/rakmu/arena_upload.py` | 라크무 런처에 넣을 자동 업로드 모듈 |
| `launcher/rts-arena/` | **RTS-Arena 통합런처** (게임 설치·실행 데스크톱 앱). 웹 서버와 별개 — 태그 `arena-launcher-v*` 로 GitHub Release 배포. [README](launcher/rts-arena/README.md) |
| `deploy/compose.yaml` | NAS Portainer 스택 |
| `docs/` | 런처 연동 방법, 라크무 서버 수정 요청 |
| `tests/` | `python -m unittest discover -s tests -v` |
| `tools/demo_seed.py` | 화면 확인용 가짜 경기 |

## 빠른 실행 (Windows, PowerShell)

```powershell
python tools/demo_seed.py
```

```powershell
$env:ARENA_DATA_DIR="data-demo"; python -m arena
```

브라우저에서 http://127.0.0.1:8080
