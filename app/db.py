import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from db.migrate import apply_pending

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    join_code TEXT NOT NULL UNIQUE,
    teacher_key_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    nickname TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (class_id, nickname)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 교사 로그인 세션. 학급 코드 + 교사 키로 로그인하며, 토큰은 그 학급 하나만 열어 준다.
CREATE TABLE IF NOT EXISTS teacher_sessions (
    token_hash TEXT PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 코드 하나를 다 고칠 때까지의 제출 묶음. 고칠 때마다 원래 코드를 덮어쓰지 않고
-- 같은 스레드에 새 제출이 쌓인다. status: open(고치는 중) / fixed(다 고침) / dropped(그만둠)
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'fixed', 'dropped')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    code TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    thread_id INTEGER REFERENCES threads(id)
);

-- 분석기가 만든 의심 지점 하나. steps_json 은 단계별 힌트 목록이고,
-- revealed_steps 만큼만 학생에게 공개된다 (FR-1.5).
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    case_id TEXT NOT NULL,
    line INTEGER,
    steps_json TEXT NOT NULL,
    revealed_steps INTEGER NOT NULL DEFAULT 0
);

-- 도우미 학생(helper)이 스레드 주인을 돕는 짝. 스레드 하나에 도우미 한 명,
-- 도우미 한 명에 진행 중인 짝 하나만 허용한다 (동시에 두 요청이 와도 DB 가 막아 준다).
CREATE TABLE IF NOT EXISTS help_sessions (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id),
    helper_id INTEGER NOT NULL REFERENCES students(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_help_per_thread
    ON help_sessions(thread_id) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS one_active_help_per_helper
    ON help_sessions(helper_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    help_session_id INTEGER NOT NULL REFERENCES help_sessions(id),
    sender_id INTEGER NOT NULL REFERENCES students(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def db_path() -> Path:
    return Path(os.environ.get("CWA_DB_PATH", DEFAULT_DB_PATH))


def get_connection() -> sqlite3.Connection:
    # 동기 엔드포인트는 스레드 풀에서 실행되어 의존성의 시작과 종료가 다른 스레드일 수 있다.
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """스레드가 생기기 전에 만든 DB 를 새 구조로 올린다. 이미 올렸다면 아무것도 하지 않는다."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(submissions)")}
    if "thread_id" not in columns:
        conn.execute("ALTER TABLE submissions ADD COLUMN thread_id INTEGER REFERENCES threads(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_submissions_thread ON submissions(thread_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_threads_student ON threads(student_id, status)")

    # 예전 제출은 하나씩 '그만둔' 스레드에 담는다 (완성한 기록으로 세면 안 되므로 fixed 가 아니다).
    legacy = conn.execute(
        "SELECT id, student_id, created_at FROM submissions WHERE thread_id IS NULL"
    ).fetchall()
    for row in legacy:
        cur = conn.execute(
            "INSERT INTO threads (student_id, status, created_at, closed_at) VALUES (?, 'dropped', ?, ?)",
            (row["student_id"], row["created_at"], row["created_at"]),
        )
        conn.execute("UPDATE submissions SET thread_id = ? WHERE id = ?", (cur.lastrowid, row["id"]))


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        # 교사 계정·단원·통계 뷰 같은 이후 단계는 db/ 의 마이그레이션이 올린다 (이미 올렸으면 건너뜀).
        apply_pending(conn)
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
