-- dashboard_queries.sql : 교사 대시보드가 쓰는 질의 모음 (v3 뷰 기준).
-- 이름 붙은 매개변수(:teacher_id 등)는 sqlite3 의 딕셔너리 바인딩으로 넘긴다.
-- 각 질의는 '-- name: ' 줄로 시작한다. 교사 API 는 세션의 class_id 로만 질의해야 한다
-- (다른 학급 데이터에 접근 경로가 없어야 한다 — CLAUDE.md 의 규칙).

-- name: my_classes
-- 1단계: 교사가 맡은 학급 목록. 학급 카드마다 학생 수·오답률·현재 단원 완료율을 보여 준다.
SELECT * FROM v_class_overview
WHERE teacher_id = :teacher_id
ORDER BY class_name;

-- name: students_in_class
-- 2단계: 학급 안의 학생별 학습 현황. 오답률이 높은 학생이 위, 아직 제출 안 한 학생은 아래.
SELECT student_id, nickname, submissions, error_submissions, error_rate_pct,
       fixed_threads, open_threads, dropped_threads,
       top_case_title, current_unit_progress_pct, last_submission_at
FROM v_student_stats
WHERE class_id = :class_id
ORDER BY error_rate_pct IS NULL, error_rate_pct DESC, nickname;

-- name: student_case_ranking
-- 학생 한 명의 오답 유형 순위 (1위가 가장 많이 겪은 유형).
SELECT rank_in_student AS rank, case_id, category, title, thread_count, finding_count, share_pct
FROM v_student_case_rank
WHERE class_id = :class_id AND student_id = :student_id
ORDER BY rank_in_student, case_id;

-- name: class_case_ranking
-- 학급 전체의 오답 유형 순위 (많은 학생이 어려워한 유형이 위).
SELECT rank_in_class AS rank, case_id, category, title, students, thread_count, finding_count
FROM v_class_case_rank
WHERE class_id = :class_id
ORDER BY rank_in_class, case_id;

-- name: class_progress
-- 학급 전체 진도: 단원 순서대로 완료한 학생 수와 평균 진도.
SELECT unit_id, unit_title, unit_status, target_fixed,
       students_total, students_done, students_started, completion_pct, avg_progress_pct
FROM v_class_unit_progress
WHERE class_id = :class_id
ORDER BY unit_position, unit_id;

-- name: student_progress_in_unit
-- 한 단원에서 학생별 진도 (아직 시작 안 한 학생도 0% 로 나온다).
SELECT student_id, nickname, started_threads, fixed_threads, progress_pct, is_done
FROM v_student_unit_progress
WHERE class_id = :class_id AND unit_id = :unit_id
ORDER BY progress_pct, nickname;

-- name: error_rate_by_day
-- 기간 추이: 날짜별 제출 수와 오답률 (한국 시간 기준 날짜). :since 는 'YYYY-MM-DD HH:MM:SS' UTC.
SELECT date(r.created_at, '+9 hours') AS day,
       COUNT(*) AS submissions,
       SUM(r.is_error) AS error_submissions,
       ROUND(100.0 * SUM(r.is_error) / COUNT(*), 1) AS error_rate_pct
FROM v_submission_result r
JOIN students st ON st.id = r.student_id
WHERE st.class_id = :class_id AND r.created_at >= :since
GROUP BY day
ORDER BY day;

-- name: set_current_unit
-- 진도 관리: 학급이 지금 나가는 단원을 바꾼다. 이후 새로 시작하는 코드(스레드)가 그 단원에 묶인다.
-- (그 학급의 단원이 아니면 트리거가 거절한다.)
UPDATE classes SET current_unit_id = :unit_id WHERE id = :class_id;

-- name: set_unit_status
-- 진도 관리: 단원 상태를 planned -> active -> done 으로 바꾼다.
UPDATE units SET status = :status WHERE id = :unit_id AND class_id = :class_id;
