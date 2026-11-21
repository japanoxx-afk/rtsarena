"""SQLite 저장소 (표준 라이브러리만 사용 — NAS 에서 pip 설치 없이 python:3.12-slim 으로 실행)."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("arena.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game          TEXT NOT NULL,
    hash          TEXT NOT NULL UNIQUE,          -- 공개 주소용 (/download/<hash>)
    seed          INTEGER,
    map_path      TEXT NOT NULL,
    map_name      TEXT NOT NULL,
    played_at     TEXT NOT NULL,                 -- 'YYYY-MM-DD HH:MM:SS' (한국 시간)
    duration      REAL NOT NULL DEFAULT 0,
    gametype      TEXT NOT NULL DEFAULT 'normal',-- normal | rank
    result_source TEXT,                          -- server | replay | NULL(모름)
    server_ref    TEXT,                          -- 로비 서버 matches.json 기록 키 (time|players)
    downloads     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_matches_game_time ON matches(game, played_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_matches_server_ref ON matches(game, server_ref) WHERE server_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS match_players (
    match_id  INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    slot      INTEGER NOT NULL,
    name      TEXT NOT NULL,
    race      INTEGER,
    result    TEXT,                               -- win | lose | draw | NULL
    PRIMARY KEY (match_id, slot)
);
CREATE INDEX IF NOT EXISTS ix_mp_name ON match_players(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS replays (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    sha256      TEXT NOT NULL UNIQUE,
    filename    TEXT NOT NULL,                    -- 내려받을 때 이름
    stored_path TEXT NOT NULL,                    -- data/replays 아래 상대 경로
    size        INTEGER NOT NULL,
    duration    REAL NOT NULL DEFAULT 0,
    uploader    TEXT,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_replays_match ON replays(match_id);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        # NAS 공유 폴더(VM 에 네트워크로 연결된 볼륨)에서는 WAL(공유 메모리 파일)이 동작하지 않는 경우가 많아
        # 기본은 일반 저널(DELETE). 로컬 디스크에서만 ARENA_SQLITE_WAL=1 로 WAL 을 켤 수 있다.
        mode = "WAL" if os.environ.get("ARENA_SQLITE_WAL") == "1" else "DELETE"
        try:
            self.conn.execute(f"PRAGMA journal_mode={mode}")
        except sqlite3.OperationalError as e:
            log.warning("journal_mode=%s 실패(%s) — 기본값 사용", mode, e)
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        log.info("DB 준비: %s (journal_mode=%s)", path,
                 self.conn.execute("PRAGMA journal_mode").fetchone()[0])

    def q(self, sql: str, args=()) -> list:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def run(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    def tx(self):
        """with db.tx(): ... — 여러 문장을 한 번에 커밋."""
        return _Tx(self)


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()
        return False
