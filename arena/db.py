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
        # NAS 공유 폴더(VM 에 네트워크로 연결된 볼륨)에서는 WAL 이 'database is locked' 로 실패한다 (2026-11-21 실제 확인).
        # 기본은 일반 저널(DELETE). 그래도 파일 잠금 자체가 안 되는 마운트면 잠금 없이 연다:
        # 이 사이트는 프로세스 1개가 연결 1개를 RLock 으로 나눠 쓰므로 잠금이 없어도 안전하다.
        # 강제: ARENA_SQLITE_NOLOCK=1 (처음부터 잠금 없이), ARENA_SQLITE_WAL=1 (로컬 디스크에서만).
        attempts = [False, True]
        if os.environ.get("ARENA_SQLITE_NOLOCK") == "1":
            attempts = [True]
        last = None
        for nolock in attempts:
            try:
                self.conn = self._open(path, nolock)
                self.nolock = nolock
                break
            except sqlite3.OperationalError as e:
                last = e
                log.warning("DB 열기 실패(nolock=%s): %s", nolock, e)
        else:
            raise last
        log.info("DB 준비: %s (journal_mode=%s, 파일잠금=%s)", path,
                 self.conn.execute("PRAGMA journal_mode").fetchone()[0], "끔" if self.nolock else "켬")

    @staticmethod
    def _open(path: Path, nolock: bool) -> sqlite3.Connection:
        uri = path.resolve().as_uri() + ("?nolock=1" if nolock else "")
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            mode = "WAL" if os.environ.get("ARENA_SQLITE_WAL") == "1" and not nolock else "DELETE"
            conn.execute(f"PRAGMA journal_mode={mode}")
            conn.executescript(SCHEMA)
            conn.execute("SELECT COUNT(*) FROM matches").fetchone()   # 실제로 읽히는지
            return conn
        except sqlite3.OperationalError:
            conn.close()
            raise

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
