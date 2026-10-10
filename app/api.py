import json
import secrets
import sqlite3

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from . import analysis
from .auth import SESSION_COOKIE, SESSION_MAX_AGE, current_student, hash_token, new_token
from .db import get_db
from .hub import publish
from .threads import active_session_id, close_thread, finding_out, submission_out

router = APIRouter(prefix="/api")

# 헷갈리기 쉬운 글자(0/O, 1/I)를 뺀 학급 코드용 문자
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6


def _not_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("빈 값은 사용할 수 없습니다.")
    return value


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)

    _strip_name = field_validator("name")(_not_blank)


class JoinRequest(BaseModel):
    join_code: str = Field(min_length=1, max_length=20)
    nickname: str = Field(min_length=1, max_length=20)

    _strip_nickname = field_validator("nickname")(_not_blank)


class SubmissionCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20000)
    # 고치던 코드에 새 버전을 쌓을 때 그 스레드의 id. 없으면 새 코드로 시작한다.
    thread_id: int | None = None

    @field_validator("code")
    @classmethod
    def code_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("빈 코드는 제출할 수 없습니다.")
        return value  # 들여쓰기가 의미 있으므로 원문 그대로 저장한다.


def insert_class(db: sqlite3.Connection, name: str, teacher_id: int | None = None) -> dict:
    """학급 코드와 교사 키를 만들어 학급을 저장한다. 교사 계정으로 만들면 teacher_id 를 함께 둔다."""
    teacher_key = secrets.token_urlsafe(16)
    for _ in range(10):
        join_code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))
        try:
            with db:
                cur = db.execute(
                    "INSERT INTO classes (name, join_code, teacher_key_hash, teacher_id) VALUES (?, ?, ?, ?)",
                    (name, join_code, hash_token(teacher_key), teacher_id),
                )
        except sqlite3.IntegrityError:  # 학급 코드 중복
            continue
        # 교사 키는 해시만 저장하므로 지금 응답에서만 볼 수 있다.
        return {"id": cur.lastrowid, "name": name, "join_code": join_code, "teacher_key": teacher_key}
    raise HTTPException(status_code=500, detail="학급 코드를 만들지 못했습니다. 다시 시도해 주세요.")


@router.post("/classes", status_code=201)
def create_class(body: ClassCreate, db: sqlite3.Connection = Depends(get_db)):
    created = insert_class(db, body.name)
    return {"name": created["name"], "join_code": created["join_code"], "teacher_key": created["teacher_key"]}


@router.post("/join")
def join(body: JoinRequest, response: Response, db: sqlite3.Connection = Depends(get_db)):
    cls = db.execute(
        "SELECT id, name FROM classes WHERE join_code = ?", (body.join_code.strip().upper(),)
    ).fetchone()
    if cls is None:
        raise HTTPException(status_code=404, detail="학급 코드를 찾을 수 없습니다.")

    token = new_token()
    with db:
        # 같은 닉네임이 이미 있으면 그 학생으로 다시 입장한다 (비밀번호 없는 방식).
        db.execute(
            "INSERT OR IGNORE INTO students (class_id, nickname) VALUES (?, ?)",
            (cls["id"], body.nickname),
        )
        student = db.execute(
            "SELECT id, nickname FROM students WHERE class_id = ? AND nickname = ?",
            (cls["id"], body.nickname),
        ).fetchone()
        db.execute(
            "INSERT INTO sessions (token_hash, student_id) VALUES (?, ?)",
            (hash_token(token), student["id"]),
        )
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax"
    )
    return {"nickname": student["nickname"], "class_name": cls["name"]}


@router.get("/me")
def me(student: sqlite3.Row = Depends(current_student)):
    return {"nickname": student["nickname"], "class_name": student["class_name"]}


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    if session:
        with db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(session),))
    response.delete_cookie(SESSION_COOKIE)


@router.post("/submissions", status_code=201)
def create_submission(
    body: SubmissionCreate,
    db: sqlite3.Connection = Depends(get_db),
    student: sqlite3.Row = Depends(current_student),
):
    # 친구를 도와주는 중에는 화면이 그 친구의 코드를 보여 주므로 내 코드는 나중에 제출한다.
    if db.execute(
        "SELECT 1 FROM help_sessions WHERE helper_id = ? AND status = 'active'", (student["id"],)
    ).fetchone():
        raise HTTPException(
            status_code=409,
            detail="친구를 도와주는 중에는 내 코드를 제출할 수 없어요. 도움을 마친 뒤 제출해 주세요.",
        )

    findings = analysis.analyze(body.code)
    ended: list[tuple[int, str]] = []  # (끝난 도움 짝, 이유)
    with db:
        if body.thread_id is None:
            # 새 코드로 시작하면 고치던 코드는 그만둔 것으로 본다.
            for old in db.execute(
                "SELECT id FROM threads WHERE student_id = ? AND status = 'open'", (student["id"],)
            ).fetchall():
                ended += [(sid, "dropped") for sid in close_thread(db, old["id"], "dropped")]
            thread_id = db.execute(
                "INSERT INTO threads (student_id) VALUES (?)", (student["id"],)
            ).lastrowid
            watching = None
        else:
            thread = db.execute(
                "SELECT id, status FROM threads WHERE id = ? AND student_id = ?",
                (body.thread_id, student["id"]),
            ).fetchone()
            if thread is None:
                raise HTTPException(status_code=404, detail="고치던 코드를 찾을 수 없습니다.")
            if thread["status"] != "open":
                raise HTTPException(status_code=409, detail="이미 끝난 코드예요. 새 코드로 시작해 주세요.")
            last = db.execute(
                "SELECT code FROM submissions WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
                (thread["id"],),
            ).fetchone()
            if last is not None and last["code"] == body.code:
                raise HTTPException(status_code=422, detail="이전 제출과 똑같은 코드예요. 고친 뒤 제출해 주세요.")
            thread_id = thread["id"]
            watching = active_session_id(db, thread_id)

        submission_id = db.execute(
            "INSERT INTO submissions (student_id, code, thread_id) VALUES (?, ?, ?)",
            (student["id"], body.code, thread_id),
        ).lastrowid
        for f in findings:
            steps = [{"label": s.label, "text": s.text} for s in f.steps]
            db.execute(
                "INSERT INTO findings (submission_id, case_id, line, steps_json) VALUES (?, ?, ?, ?)",
                (submission_id, f.case_id, f.line, json.dumps(steps, ensure_ascii=False)),
            )

        thread_status = "open"
        if not findings:  # 남은 오류가 없으면 다 고친 것이다.
            ended += [(sid, "fixed") for sid in close_thread(db, thread_id, "fixed")]
            thread_status = "fixed"

    # 도와주는 친구 화면에 새 버전을 보여 주고, 짝이 끝났다면 알린다.
    if watching is not None:
        publish(watching, {"type": "submission"})
    for session_id, reason in ended:
        publish(session_id, {"type": "ended", "reason": reason})

    submission = db.execute(
        "SELECT id, code, created_at FROM submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    return {**submission_out(db, submission), "thread_id": thread_id, "thread_status": thread_status}


@router.get("/submissions")
def list_submissions(
    db: sqlite3.Connection = Depends(get_db),
    student: sqlite3.Row = Depends(current_student),
):
    rows = db.execute(
        "SELECT id, code, created_at FROM submissions WHERE student_id = ? ORDER BY id DESC",
        (student["id"],),
    ).fetchall()
    result = []
    for row in rows:
        first_line = next((ln.strip() for ln in row["code"].splitlines() if ln.strip()), "")
        result.append({"id": row["id"], "created_at": row["created_at"], "preview": first_line[:60]})
    return result


@router.get("/submissions/{submission_id}")
def get_submission(
    submission_id: int,
    db: sqlite3.Connection = Depends(get_db),
    student: sqlite3.Row = Depends(current_student),
):
    # 남의 제출은 존재 여부도 알 수 없도록 404 로 응답한다.
    submission = db.execute(
        "SELECT id, code, created_at FROM submissions WHERE id = ? AND student_id = ?",
        (submission_id, student["id"]),
    ).fetchone()
    if submission is None:
        raise HTTPException(status_code=404, detail="제출을 찾을 수 없습니다.")
    return submission_out(db, submission)


@router.post("/findings/{finding_id}/reveal")
def reveal_next_step(
    finding_id: int,
    db: sqlite3.Connection = Depends(get_db),
    student: sqlite3.Row = Depends(current_student),
):
    # 코드 주인과, 그 코드를 지금 도와주는 친구가 함께 한 단계씩 연다 (FR-2.4).
    query = """
        SELECT f.id, f.steps_json, f.revealed_steps, s.thread_id
        FROM findings f JOIN submissions s ON s.id = f.submission_id
        WHERE f.id = ? AND (
            s.student_id = ?
            OR EXISTS (
                SELECT 1 FROM help_sessions h
                WHERE h.thread_id = s.thread_id AND h.helper_id = ? AND h.status = 'active'
            )
        )
    """
    args = (finding_id, student["id"], student["id"])
    row = db.execute(query, args).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="확인할 항목을 찾을 수 없습니다.")
    total = len(json.loads(row["steps_json"]))
    with db:
        db.execute(
            "UPDATE findings SET revealed_steps = revealed_steps + 1 WHERE id = ? AND revealed_steps < ?",
            (finding_id, total),
        )
    session_id = active_session_id(db, row["thread_id"])
    if session_id is not None:
        publish(session_id, {"type": "hint"})
    return finding_out(db.execute(query, args).fetchone())
