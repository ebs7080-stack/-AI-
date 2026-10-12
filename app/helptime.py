"""교실의 도움 시간.

코드 제출과 힌트는 언제나 되지만, '친구 도와주러 가기'와 채팅은 교사가 연 도움 시간 동안만 된다.
교실마다 끝나는 시각(classes.help_until, UTC) 하나만 저장하고, 그 시각이 지나면 닫힌 것으로 본다.
시간이 지났을 때 남아 있는 도움 짝은 따로 도는 타이머 없이, 학생 화면이 상태를 물어볼 때 끝낸다.
"""

import sqlite3

MAX_MINUTES = 180  # 한 번에 열거나 늘릴 수 있는 시간


def state(db: sqlite3.Connection, class_id: int) -> dict:
    row = db.execute(
        """
        SELECT help_until,
               CAST(strftime('%s', help_until) AS INTEGER) - CAST(strftime('%s', 'now') AS INTEGER) AS remaining
        FROM classes WHERE id = ?
        """,
        (class_id,),
    ).fetchone()
    remaining = row["remaining"] if row and row["remaining"] is not None else 0
    if remaining <= 0:
        return {"active": False, "ends_at": None, "remaining_seconds": 0}
    return {
        "active": True,
        "ends_at": row["help_until"].replace(" ", "T") + "+00:00",
        # 브라우저 시계가 틀릴 수 있어 화면은 끝나는 시각 대신 이 값으로 남은 시간을 센다.
        "remaining_seconds": remaining,
    }


def add_minutes(db: sqlite3.Connection, class_id: int, minutes: int) -> None:
    """도움 시간을 연다. 이미 열려 있으면 끝나는 시각을 그만큼 늦춘다."""
    db.execute(
        """
        UPDATE classes
        SET help_until = datetime(
            CASE WHEN help_until > datetime('now') THEN help_until ELSE datetime('now') END, ?
        )
        WHERE id = ?
        """,
        (f"+{minutes} minutes", class_id),
    )


def end_now(db: sqlite3.Connection, class_id: int) -> None:
    db.execute("UPDATE classes SET help_until = NULL WHERE id = ?", (class_id,))


def close_expired(db: sqlite3.Connection, class_id: int) -> list[int]:
    """도움 시간이 아닌데 남아 있는 그 교실의 도움 짝을 끝내고, 끝낸 짝의 id 를 돌려준다.

    부르는 쪽이 그 짝들의 채팅방에 {"type": "ended", "reason": "time"} 을 알린다.
    """
    if state(db, class_id)["active"]:
        return []
    with db:
        rows = db.execute(
            """
            UPDATE help_sessions SET status = 'ended', ended_at = datetime('now')
            WHERE status = 'active'
              AND helper_id IN (SELECT id FROM students WHERE class_id = ?)
            RETURNING id
            """,
            (class_id,),
        ).fetchall()
    return [row["id"] for row in rows]
