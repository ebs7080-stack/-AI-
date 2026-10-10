-- migrate_v3_to_v4.sql : 교사 계정 로그인 세션.
-- 교사 계정(teachers)으로 로그인하면 이 표에 세션이 생기고, 그 교사의 학급(classes.teacher_id)만 열린다.
-- 기존 학급 코드 + 교사 키 로그인(teacher_sessions)과는 따로 동작한다.

BEGIN;

CREATE TABLE teacher_account_sessions (
    token_hash TEXT PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_teacher_account_sessions_teacher ON teacher_account_sessions(teacher_id);

INSERT INTO schema_version (version) VALUES (4);

COMMIT;
