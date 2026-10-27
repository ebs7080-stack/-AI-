"""스레드(코드 하나를 고치는 제출 묶음)와 도움 짝에 대한 조회·갱신 함수.

api.py 와 collab.py 가 함께 쓴다. 응답 모양을 만드는 규칙(공개 전 힌트를 내보내지 않는다)도
여기 한 곳에만 둔다.
"""

import difflib
import json
import sqlite3

from . import analysis


# 교사가 이름 가리기를 켠 교실에서 짝에게 이름 대신 보여 주는 말.
ANONYMOUS = "익명"


def peer_name(db: sqlite3.Connection, student_id: int) -> str:
    """도움 짝에게 보여 줄 그 학생의 이름. 교실이 이름 가리기를 켰으면 누구인지 알려 주지 않는다."""
    row = db.execute(
        "SELECT st.name, c.hide_names FROM students st JOIN classes c ON c.id = st.class_id WHERE st.id = ?",
        (student_id,),
    ).fetchone()
    return ANONYMOUS if row["hide_names"] else row["name"]


def finding_out(row: sqlite3.Row) -> dict:
    # 공개되지 않은 힌트 본문과 case_id, 줄 번호는 응답에 절대 넣지 않는다 (FR-1.5).
    steps = json.loads(row["steps_json"])
    return {
        "id": row["id"],
        "total_steps": len(steps),
        "revealed": steps[: row["revealed_steps"]],
    }


def submission_out(db: sqlite3.Connection, submission: sqlite3.Row) -> dict:
    findings = db.execute(
        "SELECT id, case_id, steps_json, revealed_steps FROM findings WHERE submission_id = ? ORDER BY id",
        (submission["id"],),
    ).fetchall()
    return {
        "id": submission["id"],
        "code": submission["code"],
        "created_at": submission["created_at"],
        # 문법 오류라 코드가 아예 실행되지 않는다는 사실만 알린다. 어떤 오류인지(case_id)와 줄은 힌트를 열어야 나온다.
        "syntax_error": any(analysis.is_syntax_case(f["case_id"]) for f in findings),
        "findings": [finding_out(f) for f in findings],
    }


def diff_lines(old: str, new: str) -> list[dict]:
    """이전 코드에서 새 코드로 바뀐 줄을 [{op: equal|add|remove, text}] 로 돌려준다."""
    old_lines, new_lines = old.splitlines(), new.splitlines()
    result: list[dict] = []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result += [{"op": "equal", "text": t} for t in new_lines[j1:j2]]
        else:
            result += [{"op": "remove", "text": t} for t in old_lines[i1:i2]]
            result += [{"op": "add", "text": t} for t in new_lines[j1:j2]]
    return result


def thread_out(db: sqlite3.Connection, thread: sqlite3.Row, viewer_id: int) -> dict:
    rows = db.execute(
        "SELECT id, code, created_at FROM submissions WHERE thread_id = ? ORDER BY id",
        (thread["id"],),
    ).fetchall()
    submissions = []
    previous: str | None = None
    for row in rows:
        item = submission_out(db, row)
        item["diff"] = None if previous is None else diff_lines(previous, row["code"])
        previous = row["code"]
        submissions.append(item)
    return {
        "id": thread["id"],
        "status": thread["status"],
        "created_at": thread["created_at"],
        "owner": peer_name(db, thread["student_id"]),
        "is_owner": thread["student_id"] == viewer_id,
        "submissions": submissions,
    }


def viewable_thread(db: sqlite3.Connection, thread_id: int, student_id: int) -> sqlite3.Row | None:
    """주인이거나 지금 도와주는 중인 도우미만 스레드를 볼 수 있다. 그 외에는 없는 것처럼 None."""
    return db.execute(
        """
        SELECT t.id, t.student_id, t.status, t.created_at
        FROM threads t
        WHERE t.id = ? AND (
            t.student_id = ?
            OR EXISTS (
                SELECT 1 FROM help_sessions h
                WHERE h.thread_id = t.id AND h.helper_id = ? AND h.status = 'active'
            )
        )
        """,
        (thread_id, student_id, student_id),
    ).fetchone()


def active_session_id(db: sqlite3.Connection, thread_id: int) -> int | None:
    row = db.execute(
        "SELECT id FROM help_sessions WHERE thread_id = ? AND status = 'active'", (thread_id,)
    ).fetchone()
    return row["id"] if row else None


def close_thread(db: sqlite3.Connection, thread_id: int, status: str) -> list[int]:
    """스레드를 닫고 그 스레드의 도움 짝도 끝낸다. 끝난 짝의 id 를 돌려준다 (호출한 쪽이 알림을 보낸다)."""
    db.execute(
        "UPDATE threads SET status = ?, closed_at = datetime('now') WHERE id = ?",
        (status, thread_id),
    )
    ended = [
        row["id"]
        for row in db.execute(
            "SELECT id FROM help_sessions WHERE thread_id = ? AND status = 'active'", (thread_id,)
        )
    ]
    db.execute(
        "UPDATE help_sessions SET status = 'ended', ended_at = datetime('now') "
        "WHERE thread_id = ? AND status = 'active'",
        (thread_id,),
    )
    return ended
