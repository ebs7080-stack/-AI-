import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data.db"

SCHEMA = """
-- 교사 계정. 회원 가입으로 만들고 아이디 + 비밀번호로 로그인한다. 비밀번호는 해시만 저장한다.
CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 교실. 교사 한 명이 여러 개를 가질 수 있다.
-- teacher_id 가 비어 있는 행은 교사 계정이 생기기 전에 만든 교실이다 (주인이 없어 교사 화면에 나오지 않는다).
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    join_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    teacher_id INTEGER REFERENCES teachers(id),
    -- 도움 시간이 끝나는 시각(UTC). 비었거나 지났으면 친구 돕기와 채팅이 닫혀 있다.
    help_until TEXT,
    -- 1 이면 도움 짝이 된 학생들에게 서로의 이름을 가린다 (교사가 교실마다 고른다).
    hide_names INTEGER NOT NULL DEFAULT 0,
    -- 지금 수업하는 단원. 새로 시작한 코드는 이 단원의 진도로 센다 (비어 있으면 어느 단원에도 안 센다).
    -- 반드시 같은 교실의 units 행이어야 한다 (trg_classes_current_unit_* 가 막는다).
    current_unit_id INTEGER REFERENCES units(id)
);

-- 교사가 교실마다 만드는 수업 단원 (예: 변수, 반복문). 진도의 단위다.
-- target_fixed 는 그 단원에서 학생 한 명이 다 고쳐야 하는 코드 개수, 즉 진도 100% 의 기준이다.
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'active', 'done')),
    target_fixed INTEGER NOT NULL DEFAULT 1 CHECK (target_fixed >= 1),
    UNIQUE (class_id, title)
);
CREATE INDEX IF NOT EXISTS idx_units_class ON units(class_id, position);

-- 교사가 교실에 미리 등록한 학생 명단. 학생은 교실 코드 + 학번으로 로그인한다.
-- name 은 화면에 보이는 이름이고, 교사가 이름을 적지 않으면 학번이 들어간다.
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    student_no TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    name TEXT NOT NULL DEFAULT '',
    UNIQUE (class_id, student_no)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 교사가 고쳐 쓴 오류별 힌트 문구. 고친 case 만 들어 있고, 없는 case 는 cases.toml 의 기본 문구를 쓴다.
-- 교사 계정에 묶여 그 교사의 모든 교실에 적용된다.
CREATE TABLE IF NOT EXISTS teacher_hints (
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    case_id TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (teacher_id, case_id)
);

-- 교사 로그인 세션. 토큰은 그 교사의 교실들만 열어 준다.
CREATE TABLE IF NOT EXISTS teacher_sessions (
    token_hash TEXT PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 코드 하나를 다 고칠 때까지의 제출 묶음. 고칠 때마다 원래 코드를 덮어쓰지 않고
-- 같은 스레드에 새 제출이 쌓인다. status: open(고치는 중) / fixed(다 고침) / dropped(그만둠)
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'fixed', 'dropped')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    -- 시작할 때의 교실 현재 단원. 나중에 교사가 단원을 넘겨도 이미 시작한 코드의 소속은 그대로 남는다.
    unit_id INTEGER REFERENCES units(id)
);
CREATE INDEX IF NOT EXISTS idx_threads_unit ON threads(unit_id, status);

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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """그 테이블의 열 이름. 테이블이 없으면 빈 집합이다."""
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _drop_legacy_teacher_sessions(conn: sqlite3.Connection) -> None:
    """교사 키 시절의 세션(교실 하나에 묶임)은 새 구조와 맞지 않아 버린다. 교사는 다시 로그인하면 된다."""
    if "class_id" in _columns(conn, "teacher_sessions"):
        conn.execute("DROP TABLE teacher_sessions")


def _migrate_accounts(conn: sqlite3.Connection) -> None:
    """교사 키 + 닉네임 방식의 DB 를 교사 계정 + 학번 방식으로 올린다. 이미 올렸다면 아무것도 하지 않는다.

    예전 교실은 주인(교사 계정)이 없는 채로 남고, 예전 학생은 쓰던 닉네임이 그대로 학번이 된다.
    """
    classes = _columns(conn, "classes")
    if "teacher_id" not in classes:
        conn.execute("ALTER TABLE classes ADD COLUMN teacher_id INTEGER REFERENCES teachers(id)")
    if "teacher_key_hash" in classes:
        conn.execute("ALTER TABLE classes DROP COLUMN teacher_key_hash")
    if "help_until" not in classes:
        conn.execute("ALTER TABLE classes ADD COLUMN help_until TEXT")
    if "hide_names" not in classes:
        conn.execute("ALTER TABLE classes ADD COLUMN hide_names INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id)")

    students = _columns(conn, "students")
    if "nickname" in students:
        conn.execute("ALTER TABLE students RENAME COLUMN nickname TO student_no")
    if "name" not in students:
        conn.execute("ALTER TABLE students ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE students SET name = student_no WHERE name = ''")


def _migrate(conn: sqlite3.Connection) -> None:
    """스레드가 생기기 전에 만든 DB 를 새 구조로 올린다. 이미 올렸다면 아무것도 하지 않는다."""
    columns = _columns(conn, "submissions")
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


def _migrate_units(conn: sqlite3.Connection) -> None:
    """단원·진도 기능을 쓰기 전에 만든 DB 에 units 연결 열과 무결성 트리거를 붙인다.

    units 테이블 자체는 SCHEMA 가 만든다. 여기서는 그보다 먼저 만들어진 classes/threads 에
    열을 더하고, SQLite 가 제약으로는 적을 수 없는 규칙(현재 단원은 그 교실의 단원이어야 한다)을
    트리거로 세운다. 트리거는 열이 생긴 뒤에 만들어야 하므로 SCHEMA 가 아니라 이 자리에 있다.
    """
    if "current_unit_id" not in _columns(conn, "classes"):
        conn.execute("ALTER TABLE classes ADD COLUMN current_unit_id INTEGER REFERENCES units(id)")
    if "unit_id" not in _columns(conn, "threads"):
        conn.execute("ALTER TABLE threads ADD COLUMN unit_id INTEGER REFERENCES units(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_threads_unit ON threads(unit_id, status)")

    # 다른 교실의 단원을 현재 단원으로 가리키면 진도가 엉킨다. 어느 경로로 들어온 UPDATE 든 DB 가 막는다.
    for name, event in (("ins", "INSERT"), ("upd", "UPDATE OF current_unit_id")):
        conn.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_classes_current_unit_{name}
            BEFORE {event} ON classes
            WHEN NEW.current_unit_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1 FROM units u WHERE u.id = NEW.current_unit_id AND u.class_id = NEW.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'current_unit_id must belong to the class');
            END
            """
        )


def init_db() -> None:
    conn = get_connection()
    try:
        _drop_legacy_teacher_sessions(conn)
        conn.executescript(SCHEMA)
        _migrate_accounts(conn)
        _migrate(conn)
        _migrate_units(conn)
        conn.commit()
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
