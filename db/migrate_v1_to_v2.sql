-- migrate_v1_to_v2.sql : 이미 쓰던 data.db (app/db.py 의 SCHEMA 로 만든 DB) 를 v2 로 올린다.
-- 기존 데이터는 그대로 보존한다. 두 번 실행하면 threads.unit_id 추가에서 실패하므로
-- 직접 실행하지 말고 db/migrate.py 로 실행한다 (이미 올렸는지 확인한다).
-- 실행 전에 data.db 를 복사해 두는 것을 권한다.

PRAGMA foreign_keys = OFF;
BEGIN;

-- 1) 새 테이블 -------------------------------------------------------------
CREATE TABLE units (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (class_id, title)
);

CREATE TABLE error_cases (
    case_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    detection TEXT NOT NULL DEFAULT 'ast'
        CHECK (detection IN ('syntax', 'ast', 'runtime', 'llm')),
    language TEXT NOT NULL DEFAULT 'python',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
INSERT INTO error_cases (case_id, category, title, detection)
VALUES ('stub', '임시', '임시 분석 결과 (분석기 연결 전)', 'ast');

CREATE TABLE hint_reveals (
    id INTEGER PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES findings(id),
    step_index INTEGER NOT NULL CHECK (step_index >= 0),
    revealed_by INTEGER NOT NULL REFERENCES students(id),
    revealed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (finding_id, step_index)
);

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2) 기존 테이블에 열 추가 -----------------------------------------------------
ALTER TABLE threads ADD COLUMN unit_id INTEGER REFERENCES units(id);

-- 3) help_sessions 다시 만들기: helper_id 를 NULL 허용으로 + helper_kind 추가 (LLM 도우미) -----
CREATE TABLE help_sessions_new (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id),
    helper_id INTEGER REFERENCES students(id),
    helper_kind TEXT NOT NULL DEFAULT 'student' CHECK (helper_kind IN ('student', 'llm')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    CHECK ((helper_kind = 'student') = (helper_id IS NOT NULL))
);
INSERT INTO help_sessions_new (id, thread_id, helper_id, helper_kind, status, created_at, ended_at)
SELECT id, thread_id, helper_id, 'student', status, created_at, ended_at FROM help_sessions;
DROP TABLE help_sessions;
ALTER TABLE help_sessions_new RENAME TO help_sessions;
CREATE UNIQUE INDEX one_active_help_per_thread
    ON help_sessions(thread_id) WHERE status = 'active';
CREATE UNIQUE INDEX one_active_help_per_helper
    ON help_sessions(helper_id) WHERE status = 'active';

-- 4) messages 다시 만들기: sender_id 를 NULL 허용으로 + sender_kind 추가 ---------------------
CREATE TABLE messages_new (
    id INTEGER PRIMARY KEY,
    help_session_id INTEGER NOT NULL REFERENCES help_sessions(id),
    sender_id INTEGER REFERENCES students(id),
    sender_kind TEXT NOT NULL DEFAULT 'student' CHECK (sender_kind IN ('student', 'llm')),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((sender_kind = 'student') = (sender_id IS NOT NULL))
);
INSERT INTO messages_new (id, help_session_id, sender_id, sender_kind, body, created_at)
SELECT id, help_session_id, sender_id, 'student', body, created_at FROM messages;
DROP TABLE messages;
ALTER TABLE messages_new RENAME TO messages;

-- 5) 추가 도움 요청 (help_sessions 를 다시 만든 뒤에 만든다) ------------------------------------
CREATE TABLE escalations (
    id INTEGER PRIMARY KEY,
    help_session_id INTEGER NOT NULL REFERENCES help_sessions(id),
    requested_by INTEGER NOT NULL REFERENCES students(id),
    target TEXT NOT NULL CHECK (target IN ('student', 'teacher', 'llm')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'resolved', 'cancelled')),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

-- 6) 인덱스 ------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_id);
CREATE INDEX IF NOT EXISTS idx_teacher_sessions_created ON teacher_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_units_class ON units(class_id, position);
CREATE INDEX IF NOT EXISTS idx_threads_student ON threads(student_id, status);
CREATE INDEX IF NOT EXISTS idx_threads_unit ON threads(unit_id);
CREATE INDEX IF NOT EXISTS idx_submissions_thread ON submissions(thread_id);
CREATE INDEX IF NOT EXISTS idx_submissions_student_created ON submissions(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_findings_submission ON findings(submission_id);
CREATE INDEX IF NOT EXISTS idx_findings_case ON findings(case_id);
CREATE INDEX IF NOT EXISTS idx_hint_reveals_finding ON hint_reveals(finding_id);
CREATE INDEX IF NOT EXISTS idx_help_sessions_thread ON help_sessions(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(help_session_id, id);
CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status, created_at);

-- 7) 통계 뷰 -----------------------------------------------------------------------
CREATE VIEW v_class_case_stats AS
SELECT st.class_id,
       f.case_id,
       COUNT(DISTINCT s.student_id) AS students,
       COUNT(DISTINCT s.thread_id)  AS threads
FROM findings f
JOIN submissions s ON s.id = f.submission_id
JOIN students st   ON st.id = s.student_id
GROUP BY st.class_id, f.case_id;

CREATE VIEW v_unit_case_stats AS
SELECT st.class_id,
       t.unit_id,
       f.case_id,
       COUNT(DISTINCT s.student_id) AS students,
       COUNT(DISTINCT t.id)         AS threads
FROM findings f
JOIN submissions s ON s.id = f.submission_id
JOIN threads t     ON t.id = s.thread_id
JOIN students st   ON st.id = s.student_id
GROUP BY st.class_id, t.unit_id, f.case_id;

-- 8) 이미 열린 힌트는 '누가 열었는지'를 알 수 없으므로 hint_reveals 에는 옮기지 않는다.
INSERT INTO schema_version (version) VALUES (2);

COMMIT;
PRAGMA foreign_keys = ON;
