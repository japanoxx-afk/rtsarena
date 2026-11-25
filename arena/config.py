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
