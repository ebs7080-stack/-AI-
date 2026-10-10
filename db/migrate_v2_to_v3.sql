-- migrate_v2_to_v3.sql : 교사 대시보드용 구조를 v2 위에 더한다. 기존 데이터는 그대로 둔다.
-- db/migrate.py 로 실행한다 (이미 올렸는지 확인하고, 실행 전에 .bak 으로 복사한다).
--
-- 무엇이 늘었나
--   teachers        교사 계정. 한 교사가 학급 여러 개를 맡는다 (classes.teacher_id).
--   units 확장      status(planned/active/done), target_fixed(단원 완료 기준: 다 고친 코드 수)
--   classes 확장    teacher_id, current_unit_id (학급이 지금 나가는 단원)
--   트리거          새 스레드에 학급의 현재 단원을 자동으로 붙인다 -> 앱 코드를 안 고쳐도 진도가 쌓인다
--   통계 뷰         학생별 현황·오답률, 학생별/학급별 오류 유형 순위, 단원 진도, 학급 개요
--
-- 오답률 정의: 제출 중 분석기가 의심 지점(findings)을 하나라도 낸 제출의 비율.
--   (지금은 임시 분석기가 항상 finding 을 내므로 100% 로 나온다. 정적 분석기를 붙이면 의미가 생긴다.)
-- 오류 유형 순위: 같은 코드를 고치며 다시 제출해도 한 번으로 세도록 '코드(스레드) 수'가 먼저,
--   같으면 '의심 지점 수' 순이다 (app/stats.py 의 오류 유형별 집계와 같은 기준).
-- 이 파일의 뷰는 숫자와 상태만 담는다. 코드 원문·힌트 본문·채팅은 넣지 않는다 (NFR-3).

BEGIN;

-- 1) 교사 계정 ---------------------------------------------------------------------
CREATE TABLE teachers (
    id INTEGER PRIMARY KEY,
    login_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2) 기존 테이블 확장 ----------------------------------------------------------------
-- 기존 학급은 teacher_id 가 NULL 이다. 교사 키(teacher_key_hash) 로그인은 그대로 동작한다.
ALTER TABLE classes ADD COLUMN teacher_id INTEGER REFERENCES teachers(id);
ALTER TABLE classes ADD COLUMN current_unit_id INTEGER REFERENCES units(id);
ALTER TABLE units ADD COLUMN status TEXT NOT NULL DEFAULT 'planned'
    CHECK (status IN ('planned', 'active', 'done'));
ALTER TABLE units ADD COLUMN target_fixed INTEGER NOT NULL DEFAULT 1 CHECK (target_fixed >= 1);

-- 3) 트리거 ------------------------------------------------------------------------
-- 학급의 현재 단원은 그 학급의 단원이어야 한다.
CREATE TRIGGER trg_classes_current_unit_ins
BEFORE INSERT ON classes
WHEN NEW.current_unit_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM units u WHERE u.id = NEW.current_unit_id AND u.class_id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'current_unit_id must belong to the class');
END;

CREATE TRIGGER trg_classes_current_unit_upd
BEFORE UPDATE OF current_unit_id ON classes
WHEN NEW.current_unit_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM units u WHERE u.id = NEW.current_unit_id AND u.class_id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'current_unit_id must belong to the class');
END;

-- 스레드가 unit_id 없이 만들어지면 학생 학급의 현재 단원을 붙인다.
CREATE TRIGGER trg_threads_default_unit
AFTER INSERT ON threads
WHEN NEW.unit_id IS NULL
BEGIN
    UPDATE threads
       SET unit_id = (SELECT c.current_unit_id
                      FROM students st JOIN classes c ON c.id = st.class_id
                      WHERE st.id = NEW.student_id)
     WHERE id = NEW.id;
END;

-- 4) 인덱스 ------------------------------------------------------------------------
CREATE INDEX idx_classes_teacher ON classes(teacher_id);
CREATE INDEX idx_threads_unit_status ON threads(unit_id, status);

-- 5) 통계 뷰 -----------------------------------------------------------------------

-- 제출 하나의 결과: 의심 지점이 있으면 is_error = 1.
CREATE VIEW v_submission_result AS
SELECT s.id AS submission_id,
       s.student_id,
       s.thread_id,
       s.created_at,
       (SELECT COUNT(*) FROM findings f WHERE f.submission_id = s.id) AS finding_count,
       EXISTS (SELECT 1 FROM findings f WHERE f.submission_id = s.id) AS is_error
FROM submissions s;

-- 학생 x 오류 유형. rank_in_student = 1 이 그 학생이 가장 많이 겪은 유형 (동률이면 모두 1).
CREATE VIEW v_student_case_rank AS
SELECT g.class_id,
       g.student_id,
       g.case_id,
       COALESCE(ec.category, '') AS category,
       COALESCE(ec.title, g.case_id) AS title,
       g.thread_count,
       g.finding_count,
       ROUND(100.0 * g.thread_count / SUM(g.thread_count) OVER (PARTITION BY g.student_id), 1) AS share_pct,
       RANK() OVER (PARTITION BY g.student_id ORDER BY g.thread_count DESC, g.finding_count DESC) AS rank_in_student
FROM (
    SELECT st.class_id, s.student_id, f.case_id,
           COUNT(DISTINCT s.thread_id) AS thread_count,
           COUNT(*) AS finding_count
    FROM findings f
    JOIN submissions s ON s.id = f.submission_id
    JOIN students st   ON st.id = s.student_id
    GROUP BY st.class_id, s.student_id, f.case_id
) g
LEFT JOIN error_cases ec ON ec.case_id = g.case_id;

-- 학급 x 오류 유형. 학급에서 가장 많은 학생이 어려워한 유형이 위 (rank_in_class).
CREATE VIEW v_class_case_rank AS
SELECT g.class_id,
       g.case_id,
       COALESCE(ec.category, '') AS category,
       COALESCE(ec.title, g.case_id) AS title,
       g.students,
       g.thread_count,
       g.finding_count,
       RANK() OVER (PARTITION BY g.class_id ORDER BY g.students DESC, g.thread_count DESC, g.finding_count DESC) AS rank_in_class
FROM (
    SELECT st.class_id, f.case_id,
           COUNT(DISTINCT s.student_id) AS students,
           COUNT(DISTINCT s.thread_id) AS thread_count,
           COUNT(*) AS finding_count
    FROM findings f
    JOIN submissions s ON s.id = f.submission_id
    JOIN students st   ON st.id = s.student_id
    GROUP BY st.class_id, f.case_id
) g
LEFT JOIN error_cases ec ON ec.case_id = g.case_id;

-- 학생 x 단원 진도. 단원 완료 = 그 단원에서 다 고친(fixed) 코드가 target_fixed 개 이상.
-- 아직 아무것도 안 한 학생도 0% 로 나온다.
CREATE VIEW v_student_unit_progress AS
SELECT u.class_id,
       u.id AS unit_id,
       u.title AS unit_title,
       u.position AS unit_position,
       u.status AS unit_status,
       u.target_fixed,
       st.id AS student_id,
       st.nickname,
       COUNT(t.id) AS started_threads,
       COALESCE(SUM(t.status = 'fixed'), 0) AS fixed_threads,
       MIN(100.0, ROUND(100.0 * COALESCE(SUM(t.status = 'fixed'), 0) / u.target_fixed, 1)) AS progress_pct,
       COALESCE(SUM(t.status = 'fixed'), 0) >= u.target_fixed AS is_done
FROM units u
JOIN students st ON st.class_id = u.class_id
LEFT JOIN threads t ON t.unit_id = u.id AND t.student_id = st.id
GROUP BY u.id, st.id;

-- 학급 x 단원 진도 (학급 전체 진도 관리용).
CREATE VIEW v_class_unit_progress AS
SELECT class_id,
       unit_id,
       unit_title,
       unit_position,
       unit_status,
       target_fixed,
       COUNT(*) AS students_total,
       SUM(is_done) AS students_done,
       SUM(started_threads > 0) AS students_started,
       ROUND(100.0 * SUM(is_done) / COUNT(*), 1) AS completion_pct,
       ROUND(AVG(progress_pct), 1) AS avg_progress_pct
FROM v_student_unit_progress
GROUP BY class_id, unit_id;

-- 학생별 현황 (한 학급 안에서 학생 목록을 그릴 때). 학생 한 명이 한 줄.
CREATE VIEW v_student_stats AS
SELECT st.class_id,
       st.id AS student_id,
       st.nickname,
       COALESCE(sr.submissions, 0) AS submissions,
       COALESCE(sr.error_submissions, 0) AS error_submissions,
       CASE WHEN COALESCE(sr.submissions, 0) > 0
            THEN ROUND(100.0 * sr.error_submissions / sr.submissions, 1) END AS error_rate_pct,
       COALESCE(th.fixed_threads, 0) AS fixed_threads,
       COALESCE(th.open_threads, 0) AS open_threads,
       COALESCE(th.dropped_threads, 0) AS dropped_threads,
       sr.last_submission_at,
       (SELECT r.case_id FROM v_student_case_rank r
         WHERE r.student_id = st.id AND r.rank_in_student = 1
         ORDER BY r.case_id LIMIT 1) AS top_case_id,
       (SELECT r.title FROM v_student_case_rank r
         WHERE r.student_id = st.id AND r.rank_in_student = 1
         ORDER BY r.case_id LIMIT 1) AS top_case_title,
       (SELECT p.progress_pct FROM v_student_unit_progress p
         WHERE p.student_id = st.id AND p.unit_id = c.current_unit_id) AS current_unit_progress_pct
FROM students st
JOIN classes c ON c.id = st.class_id
LEFT JOIN (
    SELECT student_id,
           COUNT(*) AS submissions,
           SUM(is_error) AS error_submissions,
           MAX(created_at) AS last_submission_at
    FROM v_submission_result
    GROUP BY student_id
) sr ON sr.student_id = st.id
LEFT JOIN (
    SELECT student_id,
           SUM(status = 'fixed') AS fixed_threads,
           SUM(status = 'open') AS open_threads,
           SUM(status = 'dropped') AS dropped_threads
    FROM threads
    GROUP BY student_id
) th ON th.student_id = st.id;

-- 학급 개요 (교사가 맡은 학급 목록 화면). teacher_id 로 걸러 쓴다.
CREATE VIEW v_class_overview AS
SELECT c.id AS class_id,
       c.teacher_id,
       c.name AS class_name,
       c.join_code,
       c.current_unit_id,
       u.title AS current_unit_title,
       (SELECT COUNT(*) FROM students st WHERE st.class_id = c.id) AS students_total,
       (SELECT COUNT(DISTINCT s.student_id) FROM submissions s
          JOIN students st ON st.id = s.student_id WHERE st.class_id = c.id) AS students_active,
       COALESCE(agg.submissions, 0) AS submissions,
       COALESCE(agg.error_submissions, 0) AS error_submissions,
       CASE WHEN COALESCE(agg.submissions, 0) > 0
            THEN ROUND(100.0 * agg.error_submissions / agg.submissions, 1) END AS error_rate_pct,
       cup.completion_pct AS current_unit_completion_pct
FROM classes c
LEFT JOIN units u ON u.id = c.current_unit_id
LEFT JOIN (
    SELECT st.class_id, COUNT(*) AS submissions, SUM(r.is_error) AS error_submissions
    FROM v_submission_result r JOIN students st ON st.id = r.student_id
    GROUP BY st.class_id
) agg ON agg.class_id = c.id
LEFT JOIN v_class_unit_progress cup ON cup.class_id = c.id AND cup.unit_id = c.current_unit_id;

INSERT INTO schema_version (version) VALUES (3);

COMMIT;
