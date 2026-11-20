# rtsarena

RTS 게임 전적·리플레이 사이트. 지금은 **라크무(RhakMu)** 만 동작하고, 상단 드롭박스로 다른 RTS 를 고르도록 되어 있다
(임팩트 오브 파워는 "준비 중"으로 표시).

- 참고한 사이트: http://jw2-arena.com/matches (리더보드 / 전적기록 / 게임통계 / 맵통계, 경기 카드 + 리플레이 다운로드)
- **다음 작업자는 [HANDOFF.md](HANDOFF.md) 부터 읽는다.**

## 구성

| 경로 | 내용 |
|---|---|
| `arena/` | 웹 서버 (Python 3.12 표준 라이브러리만, SQLite) — `python -m arena` |
| `arena/games/` | 게임별 해석기. `rakmu.py` = 라크무 `.rkr` 리플레이 해석 |
| `launcher/rakmu/arena_upload.py` | 라크무 런처에 넣을 자동 업로드 모듈 |
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
