-- schema_v2.sql : 새로 만드는 DB 용 전체 스키마 (SQLite)
-- 기존 app/db.py 의 SCHEMA 위에 아래를 더했다.
--   error_cases    오류 유형 목록 (FR-1.3, FR-3.2)
--   units          단원/차시 (FR-3.3 '단원별' 통계)
--   hint_reveals   힌트를 누가 언제 열었는지 (FR-3.1, FR-2.4)
--   escalations    추가 도움 요청 (FR-2.5)
--   help_sessions / messages  LLM 도우미 지원 (FR-2.2)
--   schema_version, 인덱스, 통계 뷰
-- 시간은 모두 UTC 'YYYY-MM-DD HH:MM:SS' 문자열이다 (기존과 동일).

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- 학급·사용자

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

CREATE TABLE IF NOT EXISTS teacher_sessions (
    token_hash TEXT PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 단원/차시. 교사가 학급마다 만든다. 제출 스레드를 단원에 묶으면 단원별 통계가 된다.
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (class_id, title)
);

-- ---------------------------------------------------------------- 오류 유형

-- 오류case 목록. case_id 는 오류case.txt 를 id 로 바꾼 값이고 findings.case_id 와 이어진다.
-- 앱이 시작할 때 목록 파일에서 upsert 한다고 보고 findings 에는 FK 를 걸지 않았다
-- (분석기가 새 case 를 먼저 내보내도 제출이 실패하지 않게).
CREATE TABLE IF NOT EXISTS error_cases (
    case_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,              -- 변수 / 조건문 / 반복문 / 함수 / 중첩 반복문 ...
    title TEXT NOT NULL,                 -- 교사용 이름 (analysis.CASE_LABELS)
    detection TEXT NOT NULL DEFAULT 'ast'
        CHECK (detection IN ('syntax', 'ast', 'runtime', 'llm')),
    language TEXT NOT NULL DEFAULT 'python',   -- NFR-2: 다른 언어 확장 대비
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
INSERT OR IGNORE INTO error_cases (case_id, category, title, detection)
VALUES ('stub', '임시', '임시 분석 결과 (분석기 연결 전)', 'ast');

-- ---------------------------------------------------------------- 코드 제출

CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'fixed', 'dropped')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    unit_id INTEGER REFERENCES units(id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    code TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    thread_id INTEGER REFERENCES threads(id)
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    case_id TEXT NOT NULL,
    line INTEGER,
    steps_json TEXT NOT NULL,
    revealed_steps INTEGER NOT NULL DEFAULT 0
);

-- 힌트 단계 하나가 열린 기록. revealed_steps 는 그대로 두고(응답 속도·기존 코드 호환),
-- 이 표에는 '몇 번째 단계를 누가 언제 열었는지'를 남긴다.
CREATE TABLE IF NOT EXISTS hint_reveals (
    id INTEGER PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES findings(id),
    step_index INTEGER NOT NULL CHECK (step_index >= 0),
    revealed_by INTEGER NOT NULL REFERENCES students(id),
    revealed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (finding_id, step_index)
);

-- ---------------------------------------------------------------- 도움 짝·채팅

-- helper_kind = 'student' 이면 helper_id 필수, 'llm' 이면 helper_id 는 NULL (FR-2.2).
CREATE TABLE IF NOT EXISTS help_sessions (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id),
    helper_id INTEGER REFERENCES students(id),
    helper_kind TEXT NOT NULL DEFAULT 'student' CHECK (helper_kind IN ('student', 'llm')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    CHECK ((helper_kind = 'student') = (helper_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_help_per_thread
    ON help_sessions(thread_id) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS one_active_help_per_helper
    ON help_sessions(helper_id) WHERE status = 'active';

-- sender_kind = 'llm' 이면 sender_id 는 NULL.
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    help_session_id INTEGER NOT NULL REFERENCES help_sessions(id),
    sender_id INTEGER REFERENCES students(id),
    sender_kind TEXT NOT NULL DEFAULT 'student' CHECK (sender_kind IN ('student', 'llm')),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((sender_kind = 'student') = (sender_id IS NOT NULL))
);

-- 추가 도움 요청 (FR-2.5). 도우미가 막혔을 때 다른 학생 / 교사 / LLM 을 부른다.
CREATE TABLE IF NOT EXISTS escalations (
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

-- ---------------------------------------------------------------- 인덱스

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

-- ---------------------------------------------------------------- 통계 뷰 (FR-3.2)

-- 학급 x 오류 유형별로 만난 학생 수 / 코드(스레드) 수. 기간 필터가 필요하면 stats.py 의 질의를 쓴다.
CREATE VIEW IF NOT EXISTS v_class_case_stats AS
SELECT st.class_id,
       f.case_id,
       COUNT(DISTINCT s.student_id) AS students,
       COUNT(DISTINCT s.thread_id)  AS threads
FROM findings f
JOIN submissions s ON s.id = f.submission_id
JOIN students st   ON st.id = s.student_id
GROUP BY st.class_id, f.case_id;

-- 학급 x 단원 x 오류 유형별 집계 (단원별 통계용).
CREATE VIEW IF NOT EXISTS v_unit_case_stats AS
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

-- ---------------------------------------------------------------- 버전

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO schema_version (version) VALUES (2);
