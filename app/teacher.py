"""교사 계정, 교실과 학생 명단 관리, 교실 대시보드 (FR-3).

교사는 회원 가입으로 계정을 만들고 아이디 + 비밀번호로 로그인한다. 교실은 여러 개를 가질 수 있고,
학생이 로그인할 학번을 교실마다 미리 등록한다. 모든 교실 API 는 로그인한 교사의 교실만 다룬다.
학생 세션(cwa_session)과는 쿠키가 따로라서 학생이 교사 API 를 열 수 없다.
"""

import secrets
import sqlite3

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from .auth import (
    TEACHER_COOKIE,
    TEACHER_COOKIE_PATH,
    TEACHER_SESSION_MAX_AGE,
    current_teacher,
    hash_password,
    hash_token,
    new_token,
    owned_class,
    password_matches,
)
from . import helptime, hints
from .db import get_db
from .hub import publish
from .stats import class_dashboard, unit_progress

router = APIRouter(prefix="/api/teacher")

# 헷갈리기 쉬운 글자(0/O, 1/I)를 뺀 교실 코드용 문자
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6

MAX_ROSTER_BATCH = 200  # 한 번에 등록할 수 있는 학생 수

MAX_UNIT_TITLE = 40  # 단원 이름 길이
MAX_TARGET_FIXED = 50  # 한 단원의 목표 개수 상한
UNIT_STATUSES = ("planned", "active", "done")  # DB 의 CHECK 와 같아야 한다


def _not_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("빈 값은 사용할 수 없습니다.")
    return value


class Signup(BaseModel):
    username: str = Field(min_length=4, max_length=20, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=100)
    name: str = Field(min_length=1, max_length=20)

    _strip_name = field_validator("name")(_not_blank)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=100)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=100)
    new_password: str = Field(min_length=8, max_length=100)


class HelpTimeAdd(BaseModel):
    minutes: int = Field(ge=1, le=helptime.MAX_MINUTES)


class ClassSettings(BaseModel):
    # 도움 짝이 된 학생들에게 서로의 이름을 가릴지.
    hide_names: bool


class UnitCreate(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_UNIT_TITLE)
    # 이 단원에서 학생 한 명이 다 고쳐야 하는 코드 개수, 곧 진도 100% 의 기준.
    target_fixed: int = Field(default=1, ge=1, le=MAX_TARGET_FIXED)

    _strip_title = field_validator("title")(_not_blank)


class UnitEdit(BaseModel):
    """보낸 항목만 바꾼다. 안 보낸 항목은 그대로 둔다."""

    title: str | None = Field(default=None, min_length=1, max_length=MAX_UNIT_TITLE)
    target_fixed: int | None = Field(default=None, ge=1, le=MAX_TARGET_FIXED)
    status: str | None = None
    position: int | None = Field(default=None, ge=0, le=999)

    _strip_title = field_validator("title")(lambda v: v if v is None else _not_blank(v))

    @field_validator("status")
    @classmethod
    def _known_status(cls, value):
        if value is not None and value not in UNIT_STATUSES:
            raise ValueError("단원 상태는 planned, active, done 중 하나여야 합니다.")
        return value


class CurrentUnit(BaseModel):
    # null 을 보내면 '지금 수업하는 단원 없음' 이 된다.
    unit_id: int | None = None


class HintStepEdit(BaseModel):
    label: str = Field(min_length=1, max_length=hints.MAX_LABEL_LENGTH)
    text: str = Field(min_length=1, max_length=hints.MAX_TEXT_LENGTH)

    _strip_label = field_validator("label")(_not_blank)
    _strip_text = field_validator("text")(_not_blank)


class HintEdit(BaseModel):
    steps: list[HintStepEdit] = Field(min_length=1, max_length=hints.MAX_STEPS)


class RosterEntry(BaseModel):
    student_no: str = Field(min_length=1, max_length=20)
    name: str = Field(default="", max_length=20)

    _strip_no = field_validator("student_no")(_not_blank)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class RosterAdd(BaseModel):
    students: list[RosterEntry] = Field(min_length=1, max_length=MAX_ROSTER_BATCH)


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    students: list[RosterEntry] = Field(default_factory=list, max_length=MAX_ROSTER_BATCH)

    _strip_name = field_validator("name")(_not_blank)


def _start_session(db: sqlite3.Connection, response: Response, teacher_id: int) -> None:
    token = new_token()
    with db:
        db.execute(
            "DELETE FROM teacher_sessions WHERE created_at < datetime('now', ?)",
            (f"-{TEACHER_SESSION_MAX_AGE} seconds",),
        )
        db.execute(
            "INSERT INTO teacher_sessions (token_hash, teacher_id) VALUES (?, ?)",
            (hash_token(token), teacher_id),
        )
    response.set_cookie(
        TEACHER_COOKIE,
        token,
        max_age=TEACHER_SESSION_MAX_AGE,
        path=TEACHER_COOKIE_PATH,
        httponly=True,
        samesite="lax",
    )


# ---------- 계정 ----------


@router.post("/signup", status_code=201)
def signup(body: Signup, response: Response, db: sqlite3.Connection = Depends(get_db)):
    try:
        with db:
            teacher_id = db.execute(
                "INSERT INTO teachers (username, password_hash, name) VALUES (?, ?, ?)",
                (body.username, hash_password(body.password), body.name),
            ).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="이미 쓰고 있는 아이디예요. 다른 아이디를 골라 주세요.")
    _start_session(db, response, teacher_id)  # 가입하면 바로 로그인된다.
    return {"username": body.username, "name": body.name}


@router.post("/login")
def login(body: Login, response: Response, db: sqlite3.Connection = Depends(get_db)):
    teacher = db.execute(
        "SELECT id, username, name, password_hash FROM teachers WHERE username = ?",
        (body.username.strip(),),
    ).fetchone()
    # 아이디가 없는 경우와 비밀번호가 틀린 경우를 구별해 알려 주지 않는다.
    if not password_matches(teacher, body.password):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 맞지 않습니다.")
    _start_session(db, response, teacher["id"])
    return {"username": teacher["username"], "name": teacher["name"]}


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
    session: str | None = Cookie(default=None, alias=TEACHER_COOKIE),
):
    if session:
        with db:
            db.execute("DELETE FROM teacher_sessions WHERE token_hash = ?", (hash_token(session),))
    response.delete_cookie(TEACHER_COOKIE, path=TEACHER_COOKIE_PATH)


@router.get("/me")
def me(teacher: sqlite3.Row = Depends(current_teacher)):
    return {"username": teacher["username"], "name": teacher["name"]}


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChange,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
    session: str | None = Cookie(default=None, alias=TEACHER_COOKIE),
):
    """비밀번호를 바꾼다. 자리를 비운 사이 남이 바꾸지 못하게 지금 비밀번호를 다시 확인한다."""
    account = db.execute(
        "SELECT password_hash FROM teachers WHERE id = ?", (teacher["id"],)
    ).fetchone()
    if not password_matches(account, body.current_password):
        raise HTTPException(status_code=403, detail="지금 비밀번호가 맞지 않습니다.")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=422, detail="지금 비밀번호와 다른 비밀번호를 입력해 주세요.")
    with db:
        db.execute(
            "UPDATE teachers SET password_hash = ? WHERE id = ?",
            (hash_password(body.new_password), teacher["id"]),
        )
        # 다른 기기에 남아 있던 로그인은 모두 끊고, 지금 이 브라우저만 남긴다.
        db.execute(
            "DELETE FROM teacher_sessions WHERE teacher_id = ? AND token_hash != ?",
            (teacher["id"], hash_token(session or "")),
        )


# ---------- 교실과 학생 명단 ----------


def _add_students(db: sqlite3.Connection, class_id: int, entries: list[RosterEntry]) -> list[str]:
    """명단을 등록하고, 이미 있는 학번(같은 요청 안의 중복 포함)이라 건너뛴 학번을 돌려준다."""
    skipped = []
    for entry in entries:
        cur = db.execute(
            "INSERT OR IGNORE INTO students (class_id, student_no, name) VALUES (?, ?, ?)",
            (class_id, entry.student_no, entry.name or entry.student_no),
        )
        if cur.rowcount == 0:
            skipped.append(entry.student_no)
    return skipped


def _roster(db: sqlite3.Connection, class_id: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT st.id, st.student_no, st.name,
               EXISTS (SELECT 1 FROM threads t WHERE t.student_id = st.id)
               OR EXISTS (SELECT 1 FROM help_sessions h WHERE h.helper_id = st.id) AS has_records
        FROM students st WHERE st.class_id = ?
        ORDER BY st.student_no
        """,
        (class_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "student_no": row["student_no"],
            "name": row["name"],
            "has_records": bool(row["has_records"]),
        }
        for row in rows
    ]


def _class_out(cls: sqlite3.Row) -> dict:
    return {
        "id": cls["id"],
        "name": cls["name"],
        "join_code": cls["join_code"],
        "hide_names": bool(cls["hide_names"]),
        "current_unit_id": cls["current_unit_id"],
    }


@router.get("/classes")
def list_classes(db: sqlite3.Connection = Depends(get_db), teacher: sqlite3.Row = Depends(current_teacher)):
    rows = db.execute(
        """
        SELECT c.id, c.name, c.join_code, c.hide_names, c.current_unit_id,
               (SELECT COUNT(*) FROM students st WHERE st.class_id = c.id) AS student_count,
               (SELECT u.title FROM units u WHERE u.id = c.current_unit_id) AS current_unit_title
        FROM classes c WHERE c.teacher_id = ? ORDER BY c.id DESC
        """,
        (teacher["id"],),
    ).fetchall()
    return [
        {
            **_class_out(row),
            "current_unit_title": row["current_unit_title"],
            "student_count": row["student_count"],
            "help_time": helptime.state(db, row["id"]),
        }
        for row in rows
    ]


@router.post("/classes", status_code=201)
def create_class(
    body: ClassCreate,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
):
    for _ in range(10):
        join_code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))
        try:
            with db:
                class_id = db.execute(
                    "INSERT INTO classes (name, join_code, teacher_id) VALUES (?, ?, ?)",
                    (body.name, join_code, teacher["id"]),
                ).lastrowid
                skipped = _add_students(db, class_id, body.students)
        except sqlite3.IntegrityError:  # 교실 코드 중복
            continue
        return {
            "id": class_id,
            "name": body.name,
            "join_code": join_code,
            "hide_names": False,
            "students": _roster(db, class_id),
            "skipped": skipped,
        }
    raise HTTPException(status_code=500, detail="교실 코드를 만들지 못했습니다. 다시 시도해 주세요.")


@router.get("/classes/{class_id}")
def get_class(db: sqlite3.Connection = Depends(get_db), cls: sqlite3.Row = Depends(owned_class)):
    return {**_class_out(cls), "help_time": helptime.state(db, cls["id"]), "students": _roster(db, cls["id"])}


@router.patch("/classes/{class_id}")
def update_class(
    body: ClassSettings,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    with db:
        db.execute("UPDATE classes SET hide_names = ? WHERE id = ?", (body.hide_names, cls["id"]))
    return {**_class_out(cls), "hide_names": body.hide_names}


@router.post("/classes/{class_id}/students")
def add_students(
    body: RosterAdd,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    with db:
        skipped = _add_students(db, cls["id"], body.students)
    return {"students": _roster(db, cls["id"]), "skipped": skipped}


@router.delete("/classes/{class_id}/students/{student_id}", status_code=204)
def remove_student(
    student_id: int,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    """잘못 등록한 학번을 지운다. 이미 코드를 제출했거나 친구를 도운 학생은 기록이 남아야 해서 지우지 않는다."""
    student = next((s for s in _roster(db, cls["id"]) if s["id"] == student_id), None)
    if student is None:
        raise HTTPException(status_code=404, detail="학생을 찾을 수 없습니다.")
    if student["has_records"]:
        raise HTTPException(status_code=409, detail="이미 활동 기록이 있는 학생은 명단에서 지울 수 없어요.")
    with db:
        db.execute("DELETE FROM sessions WHERE student_id = ?", (student_id,))
        db.execute("DELETE FROM students WHERE id = ?", (student_id,))


# ---------- 오류별 힌트 문구 ----------


@router.get("/hints")
def list_hints(db: sqlite3.Connection = Depends(get_db), teacher: sqlite3.Row = Depends(current_teacher)):
    """오류 case 마다 기본 문구와, 이 교사가 고쳤다면 고친 문구."""
    return hints.list_cases(db, teacher["id"])


def _hint_or_404(db: sqlite3.Connection, teacher_id: int, case_id: str) -> dict:
    case = hints.get_case(db, teacher_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="오류 종류를 찾을 수 없습니다.")
    return case


@router.put("/hints/{case_id}")
def save_hint(
    case_id: str,
    body: HintEdit,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
):
    """그 오류의 힌트 문구를 고친다. 이 교사의 모든 교실에서 그 뒤의 새 제출부터 쓰인다."""
    _hint_or_404(db, teacher["id"], case_id)
    with db:
        hints.save(db, teacher["id"], case_id, [step.model_dump() for step in body.steps])
    return _hint_or_404(db, teacher["id"], case_id)


@router.delete("/hints/{case_id}")
def reset_hint(
    case_id: str,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
):
    """기본 문구로 되돌린다."""
    _hint_or_404(db, teacher["id"], case_id)
    with db:
        hints.reset(db, teacher["id"], case_id)
    return _hint_or_404(db, teacher["id"], case_id)


# ---------- 도움 시간 ----------


@router.post("/classes/{class_id}/help-time")
def add_help_time(
    body: HelpTimeAdd,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    """도움 시간을 그만큼 연다. 이미 열려 있으면 그만큼 늘린다 (예: 5분으로 시작, 1분 추가)."""
    with db:
        helptime.add_minutes(db, cls["id"], body.minutes)
    return helptime.state(db, cls["id"])


@router.delete("/classes/{class_id}/help-time")
def end_help_time(db: sqlite3.Connection = Depends(get_db), cls: sqlite3.Row = Depends(owned_class)):
    """도움 시간을 바로 끝낸다. 진행 중이던 도움 짝과 채팅도 함께 끝난다."""
    with db:
        helptime.end_now(db, cls["id"])
    for session_id in helptime.close_expired(db, cls["id"]):
        publish(session_id, {"type": "ended", "reason": "time"})
    return helptime.state(db, cls["id"])


# ---------- 수업 단원과 진도 ----------


def _owned_unit(db: sqlite3.Connection, class_id: int, unit_id: int) -> sqlite3.Row:
    """그 교실의 단원인지 확인한다. 다른 교실의 단원은 없는 것으로 본다."""
    row = db.execute(
        "SELECT id, title, position, status, target_fixed FROM units WHERE id = ? AND class_id = ?",
        (unit_id, class_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="단원을 찾을 수 없습니다.")
    return row


@router.get("/classes/{class_id}/units")
def list_units(db: sqlite3.Connection = Depends(get_db), cls: sqlite3.Row = Depends(owned_class)):
    """단원 목록과 단원별 진도. 대시보드가 쓰는 것과 같은 계산이다."""
    return {"current_unit_id": cls["current_unit_id"], "units": unit_progress(db, cls["id"])}


@router.post("/classes/{class_id}/units", status_code=201)
def create_unit(
    body: UnitCreate,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    """단원을 하나 더한다. 새 단원은 맨 뒤에 붙고, 첫 단원이면 바로 '지금 단원' 이 된다."""
    try:
        with db:
            position = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM units WHERE class_id = ?", (cls["id"],)
            ).fetchone()[0]
            unit_id = db.execute(
                "INSERT INTO units (class_id, title, position, target_fixed) VALUES (?, ?, ?, ?)",
                (cls["id"], body.title, position, body.target_fixed),
            ).lastrowid
            first = cls["current_unit_id"] is None
            if first:
                db.execute(
                    "UPDATE classes SET current_unit_id = ? WHERE id = ?", (unit_id, cls["id"])
                )
                db.execute("UPDATE units SET status = 'active' WHERE id = ?", (unit_id,))
    except sqlite3.IntegrityError:  # UNIQUE (class_id, title)
        raise HTTPException(status_code=409, detail="같은 이름의 단원이 이미 있어요.")
    return {
        "current_unit_id": unit_id if first else cls["current_unit_id"],
        "units": unit_progress(db, cls["id"]),
    }


@router.patch("/classes/{class_id}/units/{unit_id}")
def update_unit(
    unit_id: int,
    body: UnitEdit,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    """단원의 이름·목표 개수·상태·순서를 고친다. 목표 개수를 바꾸면 진도 퍼센트가 함께 바뀐다."""
    unit = _owned_unit(db, cls["id"], unit_id)
    changes = {field: value for field, value in body.model_dump().items() if value is not None}
    if not changes:
        raise HTTPException(status_code=422, detail="고칠 내용을 보내 주세요.")
    assigns = ", ".join(f"{field} = ?" for field in changes)
    try:
        with db:
            db.execute(
                f"UPDATE units SET {assigns} WHERE id = ?", (*changes.values(), unit["id"])
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="같은 이름의 단원이 이미 있어요.")
    return {"current_unit_id": cls["current_unit_id"], "units": unit_progress(db, cls["id"])}


@router.delete("/classes/{class_id}/units/{unit_id}")
def remove_unit(
    unit_id: int,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    """잘못 만든 단원을 지운다. 이미 그 단원으로 제출된 코드가 있으면 진도 기록이 사라지므로 지우지 않는다."""
    unit = _owned_unit(db, cls["id"], unit_id)
    used = db.execute("SELECT COUNT(*) FROM threads WHERE unit_id = ?", (unit["id"],)).fetchone()[0]
    if used:
        raise HTTPException(status_code=409, detail="이미 학생들이 제출한 단원은 지울 수 없어요.")
    with db:
        # 지우려는 단원이 '지금 단원' 이면 먼저 손을 뗀다 (FK 로 묶여 있어 순서가 중요하다).
        if cls["current_unit_id"] == unit["id"]:
            db.execute("UPDATE classes SET current_unit_id = NULL WHERE id = ?", (cls["id"],))
        db.execute("DELETE FROM units WHERE id = ?", (unit["id"],))
    current = None if cls["current_unit_id"] == unit["id"] else cls["current_unit_id"]
    return {"current_unit_id": current, "units": unit_progress(db, cls["id"])}


@router.put("/classes/{class_id}/current-unit")
def set_current_unit(
    body: CurrentUnit,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    """지금 수업하는 단원을 정한다. 이 뒤에 학생이 새로 시작하는 코드가 이 단원의 진도로 쌓인다.

    이미 시작한 코드의 단원은 바뀌지 않는다 (지난 단원의 진도를 되돌리지 않기 위해서다).
    """
    with db:
        if body.unit_id is None:
            db.execute("UPDATE classes SET current_unit_id = NULL WHERE id = ?", (cls["id"],))
        else:
            unit = _owned_unit(db, cls["id"], body.unit_id)
            db.execute(
                "UPDATE classes SET current_unit_id = ? WHERE id = ?", (unit["id"], cls["id"])
            )
            # 지금 수업하는 단원은 진행 중으로, 지나간 단원은 마친 것으로 표시해 둔다.
            db.execute(
                "UPDATE units SET status = 'done' WHERE class_id = ? AND position < ? AND status = 'active'",
                (cls["id"], unit["position"]),
            )
            db.execute("UPDATE units SET status = 'active' WHERE id = ?", (unit["id"],))
    return {"current_unit_id": body.unit_id, "units": unit_progress(db, cls["id"])}


@router.get("/classes/{class_id}/dashboard")
def dashboard(
    # 최근 며칠(오늘 포함). 없으면 전체 기간.
    days: int | None = Query(default=None, ge=1, le=365),
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    return {
        "class": _class_out(cls),
        "help_time": helptime.state(db, cls["id"]),
        **class_dashboard(db, cls["id"], days),
    }
