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
        "daily": _daily(db, params, start, today),
        # 아래 학생 상태(고치는 중 등)는 기간과 상관없이 '지금' 기준이고, 숫자만 기간을 따른다.
        "students": _students(db, params, now),
    }


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
    rows = db.execute(
        """
        SELECT st.id, st.nickname,
            (SELECT COUNT(*) FROM submissions s
              WHERE s.student_id = st.id AND s.created_at >= :cutoff) AS submissions,
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
        result.append(
            {
                "nickname": row["nickname"],
                "state": state,
                "current_attempts": current["attempts"] if current else None,
                "current_minutes": (
                    round((now - _parse(current["created_at"])).total_seconds() / 60) if current else None
                ),
                "submissions": row["submissions"],
                "fixed": row["fixed"],
                "dropped": row["dropped"],
                "helped": row["helped"],
                "was_helped": row["was_helped"],
                "last_submission_at": _parse(last).isoformat() if last else None,
            }
        )
    # 오래 붙잡고 있는 학생이 맨 위로 오게 한다. 수업 중에 가장 먼저 봐야 하는 정보다.
    result.sort(key=lambda s: (STATE_ORDER[s["state"]], -(s["current_minutes"] or 0), s["nickname"].lower()))
    return result
