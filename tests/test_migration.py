import sqlite3

from app.db import init_db

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
