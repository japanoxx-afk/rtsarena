"""설정은 모두 환경변수로 받는다 (NAS Portainer 스택의 Environment variables)."""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Config:
    def __init__(self):
        self.data_dir = Path(_env("ARENA_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
        self.host = _env("ARENA_HOST", "0.0.0.0")
        self.port = int(_env("ARENA_PORT", "8080"))
        # 런처 업로드 토큰. 비워 두면 토큰 없이 받는다(개발용). 실제 값은 저장소에 넣지 않는다.
        self.upload_token = _env("ARENA_UPLOAD_TOKEN")
        # 라크무 로비 서버의 전적 기록(승패 결과). 파일이 있으면 파일, 없으면 URL.
        self.rakmu_matches_file = _env("RAKMU_MATCHES_FILE")
        self.rakmu_matches_url = _env("RAKMU_MATCHES_URL")
        self.rakmu_sync_seconds = int(_env("RAKMU_SYNC_SECONDS", "60"))
        # 삼국지천명2 호환 서버(samchun compat_server.py)의 전적 기록 — 라크무와 같은 matches.json 형식
        self.samchun_matches_file = _env("SAMCHUN_MATCHES_FILE")
        self.samchun_matches_url = _env("SAMCHUN_MATCHES_URL")
        # 리플레이 시간과 로비 서버 기록 시간의 허용 차이(분)
        self.match_window_minutes = int(_env("ARENA_MATCH_WINDOW_MINUTES", "15"))
        self.site_name = _env("ARENA_SITE_NAME", "RTS ARENA")
        # 설치파일 다운로드 탭 링크 (NAS File Station 공유 링크). 비우면 "준비 중" 으로 보인다.
        self.rakmu_install_url = _env("RAKMU_INSTALL_URL", "https://akansas.synology.me:5001/sharing/1AywKfMKA")

        # 상단 디스코드 버튼. 비우면 숨긴다. discord.gg / discord.com 주소만 쓴다.
        self.discord_url = _env("ARENA_DISCORD_URL", "https://discord.gg/YsYpTuHCh")
        # 최신 런처 정보 (런처 자동 업데이트와 같은 파일). 비우면 최신 런처 버튼을 숨긴다.
        self.rakmu_launcher_version_url = _env(
            "RAKMU_LAUNCHER_VERSION_URL",
            "https://raw.githubusercontent.com/japanoxx-afk/new-rakum/main/version.json")

    def install_url(self, game_key: str) -> str:
        return self.rakmu_install_url if game_key == "rakmu" else ""

    def lobby_sources(self) -> dict:
        """게임 키 -> (파일, URL). 로비/호환 서버 전적을 동기화할 게임들."""
        return {"rakmu": (self.rakmu_matches_file, self.rakmu_matches_url),
                "samchun": (self.samchun_matches_file, self.samchun_matches_url)}

    @property
    def db_path(self) -> Path:
        return self.data_dir / "arena.sqlite3"

    @property
    def replay_dir(self) -> Path:
        return self.data_dir / "replays"
