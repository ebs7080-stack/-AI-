"""교사 대시보드용 학급 통계 (FR-3).

학생이 쓴 코드 원문과 채팅 내용은 다루지 않고 숫자와 상태만 돌려준다 (NFR-3). 개별 학생의
코드·대화를 교사가 볼 수 있게 할지는 요구사항이 아직 정하지 않았다 (requirements.md 4번).
모든 질의는 class_id 하나로 좁혀 다른 학급의 데이터가 섞일 수 없다.
"""

import sqlite3
import statistics
from datetime import date, datetime, time, timedelta, timezone

from .analysis import case_label

# 학교가 한국에 있다고 보고 날짜를 한국 시간으로 끊는다. DB 에는 UTC 로 저장되어 있다.
KST_OFFSET_HOURS = 9
KST = timezone(timedelta(hours=KST_OFFSET_HOURS))
DB_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

STATE_ORDER = {"fixing": 0, "helping": 1, "idle": 2}


def _parse(db_time: str) -> datetime:
    return datetime.strptime(db_time, DB_TIME_FORMAT).replace(tzinfo=timezone.utc)


def _cutoff(start: date | None) -> str:
    """기간 시작 시각(한국 자정)을 DB 시각 형식으로. 전체 기간이면 빈 문자열이라 모든 행이 통과한다."""
    if start is None:
        return ""
    return datetime.combine(start, time.min, KST).astimezone(timezone.utc).strftime(DB_TIME_FORMAT)


def class_dashboard(db: sqlite3.Connection, class_id: int, days: int | None) -> dict:
    """days 일 동안(오늘 포함)의 학급 통계. days 가 None 이면 전체 기간."""
    now = datetime.now(timezone.utc)
    today = now.astimezone(KST).date()
    start = None if days is None else today - timedelta(days=days - 1)
    params = {
        "class_id": class_id,
        "cutoff": _cutoff(start),
        "offset": f"+{KST_OFFSET_HOURS} hours",
    }
    return {
        "period": {"days": days, "start": start.isoformat() if start else None},
        "summary": _summary(db, params),
        "errors": _errors(db, params),
        # 수업 전에 한눈에 볼 요약: 지금 단원(없으면 선택한 기간)에서 가장 많이 막힌 오류 몇 개.
        "focus": _focus(db, params),
        "daily": _daily(db, params, start, today),
        # 단원 진도는 학기 내내 쌓이는 값이라 기간 필터를 걸지 않는다 (걸면 진도가 되돌아간 것처럼 보인다).
        "units": unit_progress(db, class_id),
        # 아래 학생 상태(고치는 중 등)는 기간과 상관없이 '지금' 기준이고, 숫자만 기간을 따른다.
        "students": _students(db, params, now),
    }


def _progress_pct(fixed: int, target: int) -> float:
    """목표보다 더 고쳤어도 100% 를 넘기지 않는다 (막대와 평균이 100 을 넘으면 읽기 어렵다)."""
    return min(100.0, round(100.0 * fixed / target, 1))


def unit_progress(db: sqlite3.Connection, class_id: int) -> list[dict]:
    """교실의 단원 목록과 단원별 전체 진도.

    한 단원의 진도는 '목표 개수를 다 고친 학생 수 / 등록 학생 수' 다. 학생 한 명의 진도는
    그 단원에서 다 고친 코드 수를 목표(target_fixed)로 나눈 값이고, 학급 평균도 같이 돌려준다.
    """
    students_total = db.execute(
        "SELECT COUNT(*) FROM students WHERE class_id = ?", (class_id,)
    ).fetchone()[0]
    rows = db.execute(
        """
        SELECT u.id, u.title, u.position, u.status, u.target_fixed, u.created_at,
               c.current_unit_id IS NOT NULL AND c.current_unit_id = u.id AS is_current,
               (SELECT COUNT(*) FROM threads t WHERE t.unit_id = u.id) AS threads,
               (SELECT COUNT(*) FROM threads t WHERE t.unit_id = u.id AND t.status = 'fixed') AS fixed_threads
        FROM units u JOIN classes c ON c.id = u.class_id
        WHERE u.class_id = ?
        ORDER BY u.position, u.id
        """,
        (class_id,),
    ).fetchall()

    # 학생 한 명이 한 단원에서 다 고친 코드 수. 한 번도 안 한 학생은 행이 없어 0 으로 본다.
    fixed_by = {
        (r["unit_id"], r["student_id"]): r["n"]
        for r in db.execute(
            """
            SELECT t.unit_id, t.student_id, COUNT(*) AS n
            FROM threads t JOIN students st ON st.id = t.student_id
            WHERE st.class_id = ? AND t.status = 'fixed' AND t.unit_id IS NOT NULL
            GROUP BY t.unit_id, t.student_id
            """,
            (class_id,),
        )
    }
    student_ids = [r[0] for r in db.execute("SELECT id FROM students WHERE class_id = ?", (class_id,))]

    result = []
    for row in rows:
        per_student = [fixed_by.get((row["id"], sid), 0) for sid in student_ids]
        done = sum(1 for count in per_student if count >= row["target_fixed"])
        result.append(
            {
                "id": row["id"],
                "title": row["title"],
                "position": row["position"],
                "status": row["status"],
                "target_fixed": row["target_fixed"],
                "is_current": bool(row["is_current"]),
                "threads": row["threads"],
                "fixed_threads": row["fixed_threads"],
                "students_total": students_total,
                "students_done": done,
                # 두 가지 눈금: 목표를 채운 학생 비율과, 학생별 진도의 평균.
                "done_pct": _progress_pct(done, students_total) if students_total else None,
                "avg_progress_pct": (
                    round(sum(_progress_pct(c, row["target_fixed"]) for c in per_student) / len(per_student), 1)
                    if per_student
                    else None
                ),
            }
        )
    return result


def _summary(db: sqlite3.Connection, p: dict) -> dict:
    students_total = db.execute(
        "SELECT COUNT(*) FROM students WHERE class_id = :class_id", p
    ).fetchone()[0]
    submitted = db.execute(
        """
        SELECT COUNT(*) AS submissions, COUNT(DISTINCT s.student_id) AS active_students
        FROM submissions s JOIN students st ON st.id = s.student_id
        WHERE st.class_id = :class_id AND s.created_at >= :cutoff
        """,
        p,
    ).fetchone()

    threads = {"open": 0, "fixed": 0, "dropped": 0}
    for row in db.execute(
        """
        SELECT t.status, COUNT(*) AS n
        FROM threads t JOIN students st ON st.id = t.student_id
        WHERE st.class_id = :class_id AND t.created_at >= :cutoff
        GROUP BY t.status
        """,
        p,
    ):
        threads[row["status"]] = row["n"]

    fixed = db.execute(
        """
        SELECT t.created_at, t.closed_at,
               (SELECT COUNT(*) FROM submissions s WHERE s.thread_id = t.id) AS attempts
        FROM threads t JOIN students st ON st.id = t.student_id
        WHERE st.class_id = :class_id AND t.status = 'fixed' AND t.created_at >= :cutoff
        """,
        p,
    ).fetchall()
    attempts = [row["attempts"] for row in fixed]
    minutes = [
        (_parse(row["closed_at"]) - _parse(row["created_at"])).total_seconds() / 60
        for row in fixed
        if row["closed_at"]
    ]

    help_sessions = db.execute(
        """
        SELECT COUNT(*)
        FROM help_sessions h
        JOIN threads t ON t.id = h.thread_id
        JOIN students st ON st.id = t.student_id
        WHERE st.class_id = :class_id AND h.created_at >= :cutoff
        """,
        p,
    ).fetchone()[0]

    return {
        "students_total": students_total,
        "students_active": submitted["active_students"],
        "submissions": submitted["submissions"],
        "threads": threads,
        # 다 고친 코드 하나당 제출 횟수와 걸린 시간은 이상치에 덜 흔들리도록 중앙값을 쓴다.
        "median_attempts": round(statistics.median(attempts), 1) if attempts else None,
        "median_fix_minutes": round(statistics.median(minutes), 1) if minutes else None,
        "help_sessions": help_sessions,
    }


def _errors(db: sqlite3.Connection, p: dict) -> list[dict]:
    """오류 유형별로 몇 명이, 몇 개의 코드에서 만났는지. 같은 코드를 다시 제출해도 한 번으로 센다."""
    rows = db.execute(
        """
        SELECT f.case_id,
               COUNT(DISTINCT s.student_id) AS students,
               COUNT(DISTINCT s.thread_id) AS threads
        FROM findings f
        JOIN submissions s ON s.id = f.submission_id
        JOIN students st ON st.id = s.student_id
        WHERE st.class_id = :class_id AND s.created_at >= :cutoff
        GROUP BY f.case_id
        ORDER BY students DESC, threads DESC, f.case_id
        """,
        p,
    ).fetchall()
    return [
        {
            "case_id": row["case_id"],
            "label": case_label(row["case_id"]),
            "students": row["students"],
            "threads": row["threads"],
        }
        for row in rows
    ]


FOCUS_LIMIT = 3


def _focus(db: sqlite3.Connection, p: dict) -> dict:
    """가장 많이 막힌 오류 FOCUS_LIMIT 개와, 그 오류에서 지금도 막혀 있는 학생 수.

    지금 단원이 있으면 그 단원에서 시작한 코드만 본다 (단원 진도처럼 기간 필터를 걸지 않는다:
    단원은 여러 날에 걸쳐 진행되니 '이 단원에서 학생들이 어디서 막히는가' 가 수업 준비에 맞는 물음이다).
    지금 단원이 없으면 선택한 기간 전체를 본다.

    '지금 막힘' 은 고치는 중(open)인 코드의 **마지막 제출**에 그 오류가 남아 있는 학생 수다.
    한 번 만났다가 이미 고친 학생은 세지 않는다.
    """
    unit = db.execute(
        """
        SELECT u.id, u.title FROM classes c JOIN units u ON u.id = c.current_unit_id
        WHERE c.id = :class_id
        """,
        p,
    ).fetchone()
    if unit:
        scope = "unit"
        where = "t.unit_id = :unit_id"
        q = {**p, "unit_id": unit["id"]}
    else:
        scope = "period"
        where = "s.created_at >= :cutoff"
        q = p

    rows = db.execute(
        f"""
        SELECT f.case_id,
               COUNT(DISTINCT s.student_id) AS students,
               COUNT(DISTINCT s.thread_id) AS threads
        FROM findings f
        JOIN submissions s ON s.id = f.submission_id
        JOIN threads t ON t.id = s.thread_id
        JOIN students st ON st.id = s.student_id
        WHERE st.class_id = :class_id AND {where}
        GROUP BY f.case_id
        ORDER BY students DESC, threads DESC, f.case_id
        LIMIT {FOCUS_LIMIT}
        """,
        q,
    ).fetchall()

    # 고치는 중인 코드마다 마지막 제출의 오류. 단원 범위면 그 단원의 코드만 센다.
    stuck_where = "AND t.unit_id = :unit_id" if unit else ""
    stuck = {
        r["case_id"]: r["n"]
        for r in db.execute(
            f"""
            SELECT f.case_id, COUNT(DISTINCT t.student_id) AS n
            FROM threads t
            JOIN students st ON st.id = t.student_id
            JOIN submissions s ON s.id = (
                SELECT MAX(s2.id) FROM submissions s2 WHERE s2.thread_id = t.id
            )
            JOIN findings f ON f.submission_id = s.id
            WHERE st.class_id = :class_id AND t.status = 'open' {stuck_where}
            GROUP BY f.case_id
            """,
            q,
        )
    }
    return {
        "scope": scope,
        "unit_title": unit["title"] if unit else None,
        "items": [
            {
                "case_id": row["case_id"],
                "label": case_label(row["case_id"]),
                "students": row["students"],
                "threads": row["threads"],
                "stuck_now": stuck.get(row["case_id"], 0),
            }
            for row in rows
        ],
    }


def _daily(db: sqlite3.Connection, p: dict, start: date | None, today: date) -> list[dict]:
    """하루마다 제출 수와 참여 학생 수. 제출이 없던 날도 0으로 채워 추이가 끊기지 않게 한다."""
    rows = db.execute(
        """
        SELECT date(s.created_at, :offset) AS day,
               COUNT(*) AS submissions,
               COUNT(DISTINCT s.student_id) AS students
        FROM submissions s JOIN students st ON st.id = s.student_id
        WHERE st.class_id = :class_id AND s.created_at >= :cutoff
        GROUP BY day
        """,
        p,
    ).fetchall()
    by_day = {row["day"]: row for row in rows}
    if start is None:
        if not by_day:
            return []
        start = date.fromisoformat(min(by_day))

    result = []
    day = start
    while day <= today:
        row = by_day.get(day.isoformat())
        result.append(
            {
                "date": day.isoformat(),
                "submissions": row["submissions"] if row else 0,
                "students": row["students"] if row else 0,
            }
        )
        day += timedelta(days=1)
    return result


def _students(db: sqlite3.Connection, p: dict, now: datetime) -> list[dict]:
    # 지금 단원의 목표와, 학생마다 그 단원에서 다 고친 코드 수. 단원을 안 정해 둔 교실이면 비어 있다.
    unit = db.execute(
        """
        SELECT u.id, u.title, u.target_fixed
        FROM classes c JOIN units u ON u.id = c.current_unit_id
        WHERE c.id = :class_id
        """,
        p,
    ).fetchone()
    unit_fixed = (
        {
            r["student_id"]: r["n"]
            for r in db.execute(
                """
                SELECT t.student_id, COUNT(*) AS n FROM threads t
                WHERE t.unit_id = ? AND t.status = 'fixed' GROUP BY t.student_id
                """,
                (unit["id"],),
            )
        }
        if unit
        else {}
    )

    rows = db.execute(
        """
        SELECT st.id, st.student_no, st.name,
            (SELECT COUNT(*) FROM submissions s
              WHERE s.student_id = st.id AND s.created_at >= :cutoff) AS submissions,
            (SELECT COUNT(*) FROM submissions s
              WHERE s.student_id = st.id AND s.created_at >= :cutoff
                AND EXISTS (SELECT 1 FROM findings f WHERE f.submission_id = s.id)) AS error_submissions,
            (SELECT COUNT(*) FROM threads t
              WHERE t.student_id = st.id AND t.status = 'fixed' AND t.created_at >= :cutoff) AS fixed,
            (SELECT COUNT(*) FROM threads t
              WHERE t.student_id = st.id AND t.status = 'dropped' AND t.created_at >= :cutoff) AS dropped,
            (SELECT COUNT(*) FROM help_sessions h
              WHERE h.helper_id = st.id AND h.created_at >= :cutoff) AS helped,
            (SELECT COUNT(*) FROM help_sessions h JOIN threads t ON t.id = h.thread_id
              WHERE t.student_id = st.id AND h.created_at >= :cutoff) AS was_helped,
            (SELECT MAX(created_at) FROM submissions WHERE student_id = st.id) AS last_submission_at
        FROM students st
        WHERE st.class_id = :class_id
        """,
        p,
    ).fetchall()

    # 지금 고치는 중인 코드(학생당 하나)와 지금 도와주는 중인 학생.
    fixing = {
        row["student_id"]: row
        for row in db.execute(
            """
            SELECT t.student_id, t.created_at,
                   (SELECT COUNT(*) FROM submissions s WHERE s.thread_id = t.id) AS attempts
            FROM threads t JOIN students st ON st.id = t.student_id
            WHERE st.class_id = :class_id AND t.status = 'open'
            ORDER BY t.id
            """,
            p,
        )
    }
    helping = {
        row["helper_id"]
        for row in db.execute(
            """
            SELECT h.helper_id
            FROM help_sessions h JOIN students st ON st.id = h.helper_id
            WHERE st.class_id = :class_id AND h.status = 'active'
            """,
            p,
        )
    }

    # 학생마다 기간 안에 가장 많은 코드에서 만난 오류 하나. 같은 코드를 다시 제출해도 한 번으로 센다.
    # 동점이면 case_id 순으로 하나를 고른다 (화면이 갱신될 때마다 바뀌지 않게).
    top_error: dict[int, str] = {}
    for r in db.execute(
        """
        SELECT s.student_id, f.case_id, COUNT(DISTINCT s.thread_id) AS n
        FROM findings f
        JOIN submissions s ON s.id = f.submission_id
        JOIN students st ON st.id = s.student_id
        WHERE st.class_id = :class_id AND s.created_at >= :cutoff
        GROUP BY s.student_id, f.case_id
        ORDER BY s.student_id, n DESC, f.case_id
        """,
        p,
    ):
        top_error.setdefault(r["student_id"], r["case_id"])

    result = []
    for row in rows:
        current = fixing.get(row["id"])
        if row["id"] in helping:
            state = "helping"
        elif current is not None:
            state = "fixing"
        else:
            state = "idle"
        last = row["last_submission_at"]
        unit_count = unit_fixed.get(row["id"], 0)
        result.append(
            {
                "student_no": row["student_no"],
                "name": row["name"],
                "state": state,
                # 지금 단원의 진도. 단원을 안 정해 둔 교실에서는 셋 다 None 이라 화면이 칸을 비운다.
                "unit_fixed": unit_count if unit else None,
                "unit_target": unit["target_fixed"] if unit else None,
                "unit_progress_pct": _progress_pct(unit_count, unit["target_fixed"]) if unit else None,
                "current_attempts": current["attempts"] if current else None,
                "current_minutes": (
                    round((now - _parse(current["created_at"])).total_seconds() / 60) if current else None
                ),
                "submissions": row["submissions"],
                # 오답률: 분석기가 오류를 하나라도 찾은 제출의 비율. 분석기는 확실한 것만 잡으므로
                # (미탐을 택한다) 실제 틀린 비율보다 낮게 나올 수 있다. 제출이 없으면 None.
                "error_submissions": row["error_submissions"],
                "error_rate_pct": (
                    round(100.0 * row["error_submissions"] / row["submissions"], 1) if row["submissions"] else None
                ),
                "top_error": (
                    {"case_id": top_error[row["id"]], "label": case_label(top_error[row["id"]])}
                    if row["id"] in top_error
                    else None
                ),
                "fixed": row["fixed"],
                "dropped": row["dropped"],
                "helped": row["helped"],
                "was_helped": row["was_helped"],
                "last_submission_at": _parse(last).isoformat() if last else None,
            }
        )
    # 오래 붙잡고 있는 학생이 맨 위로 오게 한다. 수업 중에 가장 먼저 봐야 하는 정보다.
    result.sort(key=lambda s: (STATE_ORDER[s["state"]], -(s["current_minutes"] or 0), s["student_no"].lower()))
    return result
