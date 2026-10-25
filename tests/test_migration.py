import sqlite3

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

# 스레드가 생기기 전의 스키마 (submissions 에 thread_id 가 없다).
LEGACY_SCHEMA = """
CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT NOT NULL, join_code TEXT NOT NULL UNIQUE,
    teacher_key_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
    nickname TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (class_id, nickname));
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE submissions (id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
    code TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE findings (id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL REFERENCES submissions(id),
    case_id TEXT NOT NULL, line INTEGER, steps_json TEXT NOT NULL, revealed_steps INTEGER NOT NULL DEFAULT 0);
"""


def test_legacy_submissions_are_moved_into_dropped_threads(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("CWA_DB_PATH", str(path))
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO classes (name, join_code, teacher_key_hash) VALUES ('반', 'ABC234', 'x')")
    conn.execute("INSERT INTO students (class_id, nickname) VALUES (1, '철수')")
    conn.execute("INSERT INTO submissions (student_id, code) VALUES (1, 'a = 1')")
    conn.execute("INSERT INTO submissions (student_id, code) VALUES (1, 'b = 2')")
    conn.commit()
    conn.close()

    init_db()
    init_db()  # 두 번 실행해도 스레드가 늘어나지 않는다

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    threads = conn.execute("SELECT id, student_id, status FROM threads ORDER BY id").fetchall()
    assert [(t["student_id"], t["status"]) for t in threads] == [(1, "dropped"), (1, "dropped")]
    linked = conn.execute("SELECT thread_id FROM submissions ORDER BY id").fetchall()
    assert [r["thread_id"] for r in linked] == [threads[0]["id"], threads[1]["id"]]
    conn.close()


def test_teacher_key_and_nickname_db_is_moved_to_accounts_and_student_numbers(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("CWA_DB_PATH", str(path))
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    # 교사 키로 로그인하던 시절의 세션 (교실 하나에 묶여 있다).
    conn.execute(
        "CREATE TABLE teacher_sessions (token_hash TEXT PRIMARY KEY, "
        "class_id INTEGER NOT NULL REFERENCES classes(id), "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute("INSERT INTO classes (name, join_code, teacher_key_hash) VALUES ('반', 'ABC234', 'x')")
    conn.execute("INSERT INTO students (class_id, nickname) VALUES (1, '철수')")
    conn.execute("INSERT INTO teacher_sessions (token_hash, class_id) VALUES ('t', 1)")
    conn.execute("INSERT INTO submissions (student_id, code) VALUES (1, 'a = 1')")
    conn.commit()
    conn.close()

    init_db()
    init_db()  # 두 번 실행해도 된다

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cls = dict(conn.execute("SELECT * FROM classes").fetchone())
    assert "teacher_key_hash" not in cls and cls["teacher_id"] is None  # 주인 없는 교실로 남는다
    student = conn.execute("SELECT student_no, name FROM students").fetchone()
    assert (student["student_no"], student["name"]) == ("철수", "철수")
    assert conn.execute("SELECT COUNT(*) FROM teacher_sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1  # 기록은 그대로다
    conn.close()

    # 예전 학생은 쓰던 닉네임을 학번 삼아 그대로 들어온다.
    with TestClient(app) as c:
        r = c.post("/api/join", json={"join_code": "ABC234", "student_no": "철수"})
        assert r.status_code == 200
        assert len(c.get("/api/submissions").json()) == 1


def test_db_from_before_units_gains_the_unit_columns_and_triggers(tmp_path, monkeypatch):
    """단원 기능이 없던 DB 를 그대로 올릴 수 있다. 기존 교실·학생·기록은 건드리지 않는다."""
    path = tmp_path / "preunits.db"
    monkeypatch.setenv("CWA_DB_PATH", str(path))
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO classes (name, join_code, teacher_key_hash) VALUES ('반', 'ABC234', 'x')")
    conn.execute("INSERT INTO students (class_id, nickname) VALUES (1, '철수')")
    conn.execute("INSERT INTO submissions (student_id, code) VALUES (1, 'a = 1')")
    conn.commit()
    conn.close()

    init_db()
    init_db()  # 두 번 실행해도 된다

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    classes = {r["name"] for r in conn.execute("PRAGMA table_info(classes)")}
    threads = {r["name"] for r in conn.execute("PRAGMA table_info(threads)")}
    assert "current_unit_id" in classes and "unit_id" in threads
    # 올리기 전의 스레드는 어느 단원에도 속하지 않는다 (없던 단원을 만들어 넣지 않는다).
    assert conn.execute("SELECT COUNT(*) FROM threads WHERE unit_id IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0

    triggers = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    assert {"trg_classes_current_unit_ins", "trg_classes_current_unit_upd"} <= triggers
    conn.close()
