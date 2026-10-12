"""고치던 코드(스레드) 조회, 도우미 매칭, 채팅.

- 도우미 A: 자기 코드를 다 고쳐서 할 일이 없는 학생이 '도와주러 가기'를 누르면, 같은 학급에서
  아직 도우미가 없는 진행 중인 코드의 주인(B) 중 무작위로 한 명과 짝이 된다 (FR-2.1).
  전에 도운 친구와 다시 짝이 될 수도 있다. 교실이 이름 가리기를 켰으면 짝에게 서로의 이름이 나가지 않는다.
- 짝이 된 동안 A 는 B 의 스레드를 볼 수 있고, 힌트를 함께 한 단계씩 연다 (FR-2.4).
- 도와주러 가기와 채팅은 교사가 연 도움 시간 동안만 된다 (helptime). 시간이 끝나면 짝도 끝난다.
- 채팅은 WebSocket 이고, 정답 코드 붙여넣기는 chat_filter 가 막는다 (FR-2.3).
"""

import random
import sqlite3
from urllib.parse import urlparse

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect

from . import chat_filter, helptime
from .auth import SESSION_COOKIE, current_student, student_for_token
from .db import get_connection, get_db
from .hub import hub, publish
from .threads import close_thread, peer_name, thread_out, viewable_thread

router = APIRouter(prefix="/api")

MAX_MESSAGE_LENGTH = 500
HISTORY_LIMIT = 100


def _own_open_thread(db: sqlite3.Connection, student_id: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT id FROM threads WHERE student_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (student_id,),
    ).fetchone()


def _helping_row(db: sqlite3.Connection, student_id: int) -> dict | None:
    row = db.execute(
        """
        SELECT h.id AS session_id, t.id AS thread_id, t.student_id AS owner_id
        FROM help_sessions h JOIN threads t ON t.id = h.thread_id
        WHERE h.helper_id = ? AND h.status = 'active'
        """,
        (student_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "session_id": row["session_id"],
        "thread_id": row["thread_id"],
        "name": peer_name(db, row["owner_id"]),
    }


@router.get("/status")
def status(db: sqlite3.Connection = Depends(get_db), student: sqlite3.Row = Depends(current_student)):
    """화면이 지금 무엇을 보여 줘야 하는지 알려 주는 요약."""
    # 도움 시간이 끝났는데 남아 있는 짝은 화면이 상태를 물어볼 때 정리한다 (5초마다 물어본다).
    for session_id in helptime.close_expired(db, student["class_id"]):
        publish(session_id, {"type": "ended", "reason": "time"})
    help_time = helptime.state(db, student["class_id"])
    open_thread = _own_open_thread(db, student["id"])
    helped_by = None
    if open_thread is not None:
        row = db.execute(
            """
            SELECT id AS session_id, helper_id FROM help_sessions
            WHERE thread_id = ? AND status = 'active'
            """,
            (open_thread["id"],),
        ).fetchone()
        if row is not None:
            helped_by = {"session_id": row["session_id"], "name": peer_name(db, row["helper_id"])}

    helping = _helping_row(db, student["id"])
    has_fixed = db.execute(
        "SELECT 1 FROM threads WHERE student_id = ? AND status = 'fixed' LIMIT 1", (student["id"],)
    ).fetchone()
    return {
        "open_thread_id": open_thread["id"] if open_thread else None,
        "helped_by": helped_by,
        "helping": helping,
        "help_time": help_time,
        # 도움 시간에, 자기 코드를 완성해 할 일이 없는 학생만 다른 친구를 도우러 갈 수 있다.
        "can_help": help_time["active"] and bool(has_fixed) and open_thread is None and helping is None,
    }


@router.get("/threads")
def list_threads(db: sqlite3.Connection = Depends(get_db), student: sqlite3.Row = Depends(current_student)):
    rows = db.execute(
        """
        SELECT t.id, t.status, t.created_at,
               (SELECT COUNT(*) FROM submissions s WHERE s.thread_id = t.id) AS submission_count,
               (SELECT code FROM submissions s WHERE s.thread_id = t.id ORDER BY s.id LIMIT 1) AS first_code
        FROM threads t WHERE t.student_id = ? ORDER BY t.id DESC
        """,
        (student["id"],),
    ).fetchall()
    result = []
    for row in rows:
        first_line = next((ln.strip() for ln in (row["first_code"] or "").splitlines() if ln.strip()), "")
        result.append(
            {
                "id": row["id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "submission_count": row["submission_count"],
                "preview": first_line[:60],
            }
        )
    return result


@router.get("/threads/{thread_id}")
def get_thread(
    thread_id: int,
    db: sqlite3.Connection = Depends(get_db),
    student: sqlite3.Row = Depends(current_student),
):
    # 남의 코드는 존재 여부도 숨기려고 404 로 응답한다. 도와주는 중인 친구는 볼 수 있다.
    thread = viewable_thread(db, thread_id, student["id"])
    if thread is None:
        raise HTTPException(status_code=404, detail="코드를 찾을 수 없습니다.")
    return thread_out(db, thread, student["id"])


@router.post("/threads/{thread_id}/finish")
def finish_thread(
    thread_id: int,
    db: sqlite3.Connection = Depends(get_db),
    student: sqlite3.Row = Depends(current_student),
):
    """'다 고쳤어요'. 분석기가 아직 실제 오류를 판정하지 못하므로 학생이 직접 끝낼 수도 있다."""
    thread = db.execute(
        "SELECT id, student_id, status, created_at FROM threads WHERE id = ? AND student_id = ?",
        (thread_id, student["id"]),
    ).fetchone()
    if thread is None:
        raise HTTPException(status_code=404, detail="코드를 찾을 수 없습니다.")
    if thread["status"] == "dropped":
        raise HTTPException(status_code=409, detail="그만둔 코드는 완성으로 바꿀 수 없어요.")
    ended: list[int] = []
    if thread["status"] == "open":
        with db:
            ended = close_thread(db, thread_id, "fixed")
        for session_id in ended:
            publish(session_id, {"type": "ended", "reason": "fixed"})
    thread = db.execute(
        "SELECT id, student_id, status, created_at FROM threads WHERE id = ?", (thread_id,)
    ).fetchone()
    return thread_out(db, thread, student["id"])


@router.post("/help/start")
def start_helping(db: sqlite3.Connection = Depends(get_db), student: sqlite3.Row = Depends(current_student)):
    """'다른 친구 도와주러 가기'. 같은 학급에서 도움이 필요한 친구를 무작위로 짝지어 준다."""
    if not helptime.state(db, student["class_id"])["active"]:
        raise HTTPException(status_code=409, detail="지금은 도움 시간이 아니에요. 선생님이 도움 시간을 열면 도와주러 갈 수 있어요.")
    if _helping_row(db, student["id"]) is not None:
        raise HTTPException(status_code=409, detail="이미 친구를 도와주는 중이에요.")
    if _own_open_thread(db, student["id"]) is not None:
        raise HTTPException(status_code=409, detail="먼저 내 코드를 다 고친 뒤에 도와주러 갈 수 있어요.")
    if not db.execute(
        "SELECT 1 FROM threads WHERE student_id = ? AND status = 'fixed' LIMIT 1", (student["id"],)
    ).fetchone():
        raise HTTPException(status_code=409, detail="내 코드를 한 번 완성해야 친구를 도와주러 갈 수 있어요.")

    candidates = db.execute(
        """
        SELECT t.id AS thread_id, owner.id AS owner_id
        FROM threads t
        JOIN students owner ON owner.id = t.student_id
        WHERE t.status = 'open'
          AND owner.class_id = ? AND owner.id != ?
          AND NOT EXISTS (SELECT 1 FROM help_sessions h WHERE h.thread_id = t.id AND h.status = 'active')
        """,
        (student["class_id"], student["id"]),
    ).fetchall()
    random.shuffle(candidates)
    for candidate in candidates:
        try:
            with db:
                cur = db.execute(
                    "INSERT INTO help_sessions (thread_id, helper_id) VALUES (?, ?)",
                    (candidate["thread_id"], student["id"]),
                )
        except sqlite3.IntegrityError:  # 그 사이 다른 도우미가 먼저 맡았다.
            continue
        return {
            "session_id": cur.lastrowid,
            "thread_id": candidate["thread_id"],
            "name": peer_name(db, candidate["owner_id"]),
        }
    raise HTTPException(status_code=404, detail="지금은 도움이 필요한 친구가 없어요.")


def _participant_session(db: sqlite3.Connection, session_id: int, student_id: int) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT h.id, h.status, h.helper_id, t.student_id AS owner_id
        FROM help_sessions h JOIN threads t ON t.id = h.thread_id
        WHERE h.id = ? AND (h.helper_id = ? OR t.student_id = ?)
        """,
        (session_id, student_id, student_id),
    ).fetchone()


@router.post("/help/{session_id}/end", status_code=204)
def end_help(
    session_id: int,
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
    student: sqlite3.Row = Depends(current_student),
):
    """도움을 마친다. 도우미와 코드 주인 누구든 끝낼 수 있다."""
    room = _participant_session(db, session_id, student["id"])
    if room is None:
        raise HTTPException(status_code=404, detail="도움 짝을 찾을 수 없습니다.")
    if room["status"] == "active":
        with db:
            db.execute(
                "UPDATE help_sessions SET status = 'ended', ended_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
        publish(session_id, {"type": "ended", "reason": "ended"})


def _message_out(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {
        "type": "message",
        "id": row["id"],
        "sender": peer_name(db, row["sender_id"]),
        "sender_id": row["sender_id"],
        "body": row["body"],
        "created_at": row["created_at"],
    }


async def _close_expired(db: sqlite3.Connection, class_id: int) -> list[int]:
    """WebSocket 쪽에서 쓰는 helptime.close_expired. 끝낸 짝의 채팅방에 알린다."""
    ended = helptime.close_expired(db, class_id)
    for session_id in ended:
        await hub.broadcast(session_id, {"type": "ended", "reason": "time"})
    return ended


@router.websocket("/help/{session_id}/ws")
async def help_chat(
    websocket: WebSocket,
    session_id: int,
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    # 다른 사이트의 페이지가 학생의 쿠키로 채팅에 붙는 것을 막는다.
    origin = websocket.headers.get("origin")
    if origin and urlparse(origin).netloc != websocket.headers.get("host"):
        await websocket.close(code=4403)
        return

    db = get_connection()
    try:
        student = student_for_token(db, session)
        if student is not None:
            await _close_expired(db, student["class_id"])
        room = _participant_session(db, session_id, student["id"]) if student else None
        if student is None or room is None or room["status"] != "active":
            await websocket.close(code=4404)
            return

        await websocket.accept()
        history = db.execute(
            """
            SELECT m.id, m.body, m.created_at, st.name, m.sender_id
            FROM messages m JOIN students st ON st.id = m.sender_id
            WHERE m.help_session_id = ? ORDER BY m.id DESC LIMIT ?
            """,
            (session_id, HISTORY_LIMIT),
        ).fetchall()
        await websocket.send_json({"type": "history", "messages": [_message_out(db, r) for r in reversed(history)]})
        hub.join(session_id, websocket)

        while True:
            try:
                data = await websocket.receive_json()
            except ValueError:  # JSON 이 아닌 메시지는 무시한다.
                continue
            body = str(data.get("body", "")).strip() if isinstance(data, dict) else ""
            if not body:
                continue

            # 도움 시간이 방금 끝났다면 이 메시지는 받지 않고 짝을 끝낸다 (양쪽에 알림이 간다).
            if session_id in await _close_expired(db, student["class_id"]):
                await websocket.close()
                return
            room = _participant_session(db, session_id, student["id"])
            if room is None or room["status"] != "active":
                await websocket.send_json({"type": "ended", "reason": "ended"})
                await websocket.close()
                return
            if len(body) > MAX_MESSAGE_LENGTH:
                await websocket.send_json(
                    {"type": "blocked", "reason": f"메시지는 {MAX_MESSAGE_LENGTH}자까지 보낼 수 있어요."}
                )
                continue
            reason = chat_filter.check_message(body)
            if reason is not None:  # 막힌 메시지는 저장도, 상대에게 전달도 하지 않는다.
                await websocket.send_json({"type": "blocked", "reason": reason})
                continue

            with db:
                message_id = db.execute(
                    "INSERT INTO messages (help_session_id, sender_id, body) VALUES (?, ?, ?)",
                    (session_id, student["id"], body),
                ).lastrowid
            row = db.execute(
                """
                SELECT m.id, m.body, m.created_at, st.name, m.sender_id
                FROM messages m JOIN students st ON st.id = m.sender_id WHERE m.id = ?
                """,
                (message_id,),
            ).fetchone()
            await hub.broadcast(session_id, _message_out(db, row))
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(session_id, websocket)
        db.close()
