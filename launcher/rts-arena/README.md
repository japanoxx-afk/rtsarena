# RTS-Arena Launcher (통합 RTS 런처)

> rtsarena 저장소의 `launcher/rts-arena/` — 전적 웹 서버(`arena/`)와 별개로 빌드·배포한다.
> 배포: `arena-launcher-v<버전>` 태그 → GitHub Actions(`.github/workflows/rts-arena-launcher.yml`)가
> Windows 에서 테스트·빌드하고 Release 에 `RTS-Arena.exe` 를 올린다.

라크무 · 태조왕건 · 삼국지 천명2 런처를 하나로 묶는 배틀넷 스타일 통합런처입니다.
1차 버전은 **라크무**를 지원하고, 나머지 두 게임은 상단 게임 바에 '준비 중'으로 표시됩니다.

## 화면 구성

- **상단 바**: RTS-ARENA 로고, 디스코드·유튜브 아이콘 (클릭하면 브라우저로 열림)
- **게임 바**: 게임 아이콘. 누르면 그 게임의 런처 화면으로 바뀝니다.
- **왼쪽**: 게임 로고 / 해상도 / `싱글플레이` · `멀티플레이` / ⚙(설치 경로)
  - 게임이 없으면 `설치` 버튼 하나만 보입니다.
- **오른쪽 위**: `맵 에디터` · `리플레이` · `런처 업데이트 확인`
- **가운데**: 업데이트 내역 (라크무 런처의 패치노트를 그대로 표시)
- **하단 상태 줄**: 다운로드·압축 해제 진행률, 취소

그 밖의 옵션(패치, hosts, 내부 해상도, 리플레이 기록 등)은 화면에 보이지 않고 라크무 런처가 자동 적용합니다.
통합런처에는 로컬 서버 기능이 없습니다 (싱글은 오프라인, 멀티는 라크무 런처에 저장된 서버 — 기본 공식 프리서버).

## 처음 실행 (게임이 없는 PC)

1. `설치` → "이미 설치되어 있어요" / "새로 설치할게요" 선택
2. 이미 설치됨 → 설치 폴더만 지정 (다운로드·압축 해제 생략)
3. 새로 설치 → 설치 위치 확인 (기본 `C:\Users\<사용자>\Downloads`, 직접 변경 가능)
   → NAS에서 ZIP 다운로드 → 압축 해제 → 게임 폴더를 찾아 설치 경로로 자동 등록
4. 설치 경로는 언제든 ⚙ → `설치 경로 변경`으로 바꿀 수 있습니다.

다운로드가 끊기면 다시 눌렀을 때 이어받습니다. ZIP 안의 한글(CP949) 파일명도 정상 처리합니다.

## 업데이트 구조

```
RTS-Arena.exe ──(arena.json)──> 통합런처 최신 버전 / NAS 설치본 주소
      │
      └─(라크무 version.json)──> RhakMuLauncher_vX.exe 를 %LOCALAPPDATA%\RTS-Arena\modules\rhakmu 에 받아 둠
                                    └─ 숨김 모드(--arena-launch 등)로 호출해 실제 게임 준비·실행
```

- 라크무 런처를 새로 배포하면(version.json 갱신) 통합런처가 시작할 때, 그리고 게임 시작 직전에 자동으로 받아 교체합니다.
- 카탈로그는 이 폴더의 `arena.json` (rtsarena main 브랜치의 raw 주소로 읽음).
  못 읽으면 내장 기본값(`arena_core/updater.py`)으로 동작합니다. NAS 주소가 바뀌면 이 파일만 고쳐 main 에 올리면 됩니다.

## 라크무 런처 쪽 작업 (연동 활성화)

통합런처는 `version.json`에 `"arena_api": 1`이 있는 라크무 런처만 숨김 모드로 실행합니다
(구버전을 숨김 인자로 실행하면 일반 런처 창이 떠 버리기 때문).

1. `bridge/arena_bridge.py` 를 new-rakum 저장소에 추가
2. `bridge/launcher.py.patch` 적용 (`if __name__ == "__main__":` 아래 3줄)
3. 빌드 후 `version.json`에 `"arena_api": 1` 추가해 배포

## 실행 / 빌드

```
run.bat              # 빌드 없이 Python 으로 바로 실행
build.bat            # 테스트 → PyInstaller → dist\RTS-Arena.exe (로컬 빌드)
```

## 배포 (새 버전)

1. `arena_core/__init__.py` 의 `ARENA_VERSION` 을 올린다 (예: 1.0.1).
2. `arena.json` 의 `arena_version` 과 `arena_url`(태그 이름 포함)을 같은 버전으로 맞춘다.
3. main 에 병합 후 `git tag arena-launcher-v1.0.1 && git push origin arena-launcher-v1.0.1`
4. Actions 가 Release 를 만들면, Release 설명의 크기·SHA256 을 `arena.json` 의 `arena_size` / `arena_sha256` 에 넣어 main 에 올린다.
   이미 설치된 통합런처는 다음 실행 때 새 버전을 안내하고 스스로 교체한다.

관리자 권한(hosts 수정·게임 패치용)은 exe 매니페스트로 자동 요청합니다.
아이콘/로고를 바꾸려면 `python tools/make_assets.py` (Pillow 필요) 후 다시 빌드하세요.

## 새 게임 추가

`arena_games/` 에 `GameModule`을 상속한 파일을 만들고 `arena_games/__init__.py`의 `MODULES`에 넣습니다.
필수 항목: `required_files`(설치 폴더 판별), `play_actions`, `check_module_update`, `notes`, `launch`.
`arena.json`의 `games.<id>`에 `version_url`, `setup_url`을 넣으면 됩니다.

## 폴더

| 경로 | 내용 |
|---|---|
| `arena.py` | 메인 화면 |
| `arena_ui.py` | 다크 테마 버튼·다이얼로그 |
| `arena_core/` | 설정, NAS 다운로드(Synology 공유 링크 해석), 압축 해제, 업데이트 |
| `arena_games/` | 게임 모듈 (`rhakmu.py`) |
| `bridge/` | 라크무 런처에 넣을 연동 코드와 패치 |
| `arena.json` | 카탈로그 (통합런처 최신 버전·게임별 주소) |
| `tests/` | 설치·연동 테스트 |
