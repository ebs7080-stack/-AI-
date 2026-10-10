"""교사 계정 로그인과 학급별 통계·진도 관리 (FR-3.1~3.3).

한 교사가 학급 여러 개를 맡고, 학급 안에서 학생별 학습 현황·오답률·오답 유형 순위와 단원 진도를 본다.
통계는 db/migrate_v2_to_v3.sql 의 뷰에서 읽는다. 숫자와 상태만 내려주고 학생의 코드 원문·힌트 본문·채팅은
넣지 않는다 (NFR-3). 다른 교사의 학급은 존재 여부도 숨기려고 403 이 아니라 404 로 답한다.

기존 학급 코드 + 교사 키 로그인(app/teacher.py)은 그대로 있고 쿠키도 따로다. 교사 키로 만들어 둔 학급은
POST /api/teacher/classes/claim 으로 계정에 연결할 수 있다.
"""

import hashlib
import re
import secrets
import sqlite3

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from .api import insert_class
from .auth import TEACHER_COOKIE_PATH, TEACHER_SESSION_MAX_AGE, hash_token, new_token, teacher_key_matches
from .db import get_db

router = APIRouter(prefix="/api/teacher")

ACCOUNT_COOKIE = "cwa_teacher_acct"
LOGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
UNIT_STATUSES = ("planned", "active", "done")

# scrypt 매개변수. 교사 계정 수가 적고 로그인이 드물어서 넉넉하게 잡았다.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return secrets.compare_digest(actual, expected)


# 없는 아이디로 로그인해도 같은 계산을 해서 응답 시간으로 계정 유무를 알아내지 못하게 한다.
_DUMMY_HASH = hash_password("dummy-password")


class Register(BaseModel):
    login_id: str
    name: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=8, max_length=100)

    @field_validator("login_id")
    @classmethod
    def valid_login_id(cls, value: str) -> str:
        value = value.strip()
        if not LOGIN_ID_PATTERN.match(value):
            raise ValueError("아이디는 영문·숫자·_ . - 로 3~30자여야 합니다.")
        return value

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("이름을 입력해 주세요.")
        return value


class Login(BaseModel):
    login_id: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=100)


class NewClass(BaseModel):
    name: str = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("빈 값은 사용할 수 없습니다.")
        return value


class ClaimClass(BaseModel):
    join_code: str = Field(min_length=1, max_length=20)
    teacher_key: str = Field(min_length=1, max_length=100)


class NewUnit(BaseModel):
    title: str = Field(min_length=1, max_length=50)
    target_fixed: int = Field(default=1, ge=1, le=50)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("단원 이름을 입력해 주세요.")
        return value


class UnitUpdate(BaseModel):
    status: str | None = None
    target_fixed: int | None = Field(default=None, ge=1, le=50)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in UNIT_STATUSES:
            raise ValueError("상태는 planned / active / done 중 하나여야 합니다.")
        return value


class CurrentUnit(BaseModel):
    unit_id: int | None  # None 이면 현재 단원을 비운다.


# ------------------------------------------------------------------ 로그인 확인

def _set_session(db: sqlite3.Connection, response: Response, teacher_id: int) -> None:
    token = new_token()
    with db:
        db.execute(
            "DELETE FROM teacher_account_sessions WHERE created_at < datetime('now', ?)",
            (f"-{TEACHER_SESSION_MAX_AGE} seconds",),
        )
        db.execute(
            "INSERT INTO teacher_account_sessions (token_hash, teacher_id) VALUES (?, ?)",
            (hash_token(token), teacher_id),
        )
    response.set_cookie(
        ACCOUNT_COOKIE,
        token,
        max_age=TEACHER_SESSION_MAX_AGE,
        path=TEACHER_COOKIE_PATH,
        httponly=True,
        samesite="lax",
    )


def current_teacher(
    db: sqlite3.Connection = Depends(get_db),
    session: str | None = Cookie(default=None, alias=ACCOUNT_COOKIE),
) -> sqlite3.Row:
    row = None
    if session:
        row = db.execute(
            """
            SELECT t.id, t.login_id, t.name
            FROM teacher_account_sessions s
            JOIN teachers t ON t.id = s.teacher_id
            WHERE s.token_hash = ? AND s.created_at >= datetime('now', ?)
            """,
            (hash_token(session), f"-{TEACHER_SESSION_MAX_AGE} seconds"),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="교사 로그인이 필요합니다.")
    return row


def owned_class(
    class_id: int,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
) -> sqlite3.Row:
    """내 학급만 연다. 남의 학급은 없는 것처럼 404 다."""
    row = db.execute(
        "SELECT id, name, join_code, current_unit_id FROM classes WHERE id = ? AND teacher_id = ?",
        (class_id, teacher["id"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="학급을 찾을 수 없습니다.")
    return row


# ------------------------------------------------------------------ 계정

@router.post("/account/register", status_code=201)
def register(body: Register, response: Response, db: sqlite3.Connection = Depends(get_db)):
    try:
        with db:
            cur = db.execute(
                "INSERT INTO teachers (login_id, name, password_hash) VALUES (?, ?, ?)",
                (body.login_id, body.name, hash_password(body.password)),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="이미 있는 아이디입니다.") from None
    _set_session(db, response, cur.lastrowid)
    return {"login_id": body.login_id, "name": body.name}


@router.post("/account/login")
def account_login(body: Login, response: Response, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT id, login_id, name, password_hash FROM teachers WHERE login_id = ?",
        (body.login_id.strip(),),
    ).fetchone()
    ok = verify_password(body.password, row["password_hash"] if row else _DUMMY_HASH)
    # 아이디가 틀린 경우와 비밀번호가 틀린 경우를 구별해 알려 주지 않는다.
    if row is None or not ok:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 맞지 않습니다.")
    _set_session(db, response, row["id"])
    return {"login_id": row["login_id"], "name": row["name"]}


@router.post("/account/logout", status_code=204)
def account_logout(
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
    session: str | None = Cookie(default=None, alias=ACCOUNT_COOKIE),
):
    if session:
        with db:
            db.execute("DELETE FROM teacher_account_sessions WHERE token_hash = ?", (hash_token(session),))
    response.delete_cookie(ACCOUNT_COOKIE, path=TEACHER_COOKIE_PATH)


@router.get("/account/me")
def account_me(teacher: sqlite3.Row = Depends(current_teacher)):
    return {"login_id": teacher["login_id"], "name": teacher["name"]}


# ------------------------------------------------------------------ 학급 목록

def _overview(row: sqlite3.Row) -> dict:
    return {
        "id": row["class_id"],
        "name": row["class_name"],
        "join_code": row["join_code"],
        "current_unit_id": row["current_unit_id"],
        "current_unit_title": row["current_unit_title"],
        "students_total": row["students_total"],
        "students_active": row["students_active"],
        "submissions": row["submissions"],
        "error_submissions": row["error_submissions"],
        "error_rate_pct": row["error_rate_pct"],
        "current_unit_completion_pct": row["current_unit_completion_pct"],
    }


@router.get("/classes")
def my_classes(db: sqlite3.Connection = Depends(get_db), teacher: sqlite3.Row = Depends(current_teacher)):
    rows = db.execute(
        "SELECT * FROM v_class_overview WHERE teacher_id = ? ORDER BY class_name, class_id",
        (teacher["id"],),
    ).fetchall()
    return {"classes": [_overview(r) for r in rows]}


@router.post("/classes", status_code=201)
def create_class(
    body: NewClass,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
):
    created = insert_class(db, body.name, teacher["id"])
    return {k: created[k] for k in ("id", "name", "join_code", "teacher_key")}


@router.post("/classes/claim")
def claim_class(
    body: ClaimClass,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
):
    """교사 키로 만들어 둔 학급을 내 계정에 연결한다. 코드와 교사 키가 모두 맞아야 한다."""
    cls = db.execute(
        "SELECT id, name, teacher_key_hash, teacher_id FROM classes WHERE join_code = ?",
        (body.join_code.strip().upper(),),
    ).fetchone()
    if not teacher_key_matches(cls, body.teacher_key.strip()):
        raise HTTPException(status_code=401, detail="학급 코드 또는 교사 키가 맞지 않습니다.")
    if cls["teacher_id"] not in (None, teacher["id"]):
        raise HTTPException(status_code=409, detail="이미 다른 선생님 계정에 연결된 학급입니다.")
    with db:
        db.execute("UPDATE classes SET teacher_id = ? WHERE id = ?", (teacher["id"], cls["id"]))
    return {"id": cls["id"], "name": cls["name"]}


# ------------------------------------------------------------------ 학급 대시보드

def _student_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["student_id"],
        "nickname": r["nickname"],
        "submissions": r["submissions"],
        "error_submissions": r["error_submissions"],
        "error_rate_pct": r["error_rate_pct"],
        "fixed_threads": r["fixed_threads"],
        "open_threads": r["open_threads"],
        "dropped_threads": r["dropped_threads"],
        "top_case_id": r["top_case_id"],
        "top_case_title": r["top_case_title"],
        "current_unit_progress_pct": r["current_unit_progress_pct"],
        "last_submission_at": r["last_submission_at"],
    }


def _unit_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["unit_id"],
        "title": r["unit_title"],
        "status": r["unit_status"],
        "target_fixed": r["target_fixed"],
        "students_total": r["students_total"],
        "students_started": r["students_started"],
        "students_done": r["students_done"],
        "completion_pct": r["completion_pct"],
        "avg_progress_pct": r["avg_progress_pct"],
    }


def _units(db: sqlite3.Connection, class_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM v_class_unit_progress WHERE class_id = ? ORDER BY unit_position, unit_id",
        (class_id,),
    ).fetchall()
    units = [_unit_row(r) for r in rows]
    # 학생이 한 명도 없으면 진도 뷰에 단원이 나오지 않으므로 단원 표에서 채운다.
    seen = {u["id"] for u in units}
    for r in db.execute(
        "SELECT id, title, status, target_fixed FROM units WHERE class_id = ? ORDER BY position, id",
        (class_id,),
    ):
        if r["id"] not in seen:
            units.append(
                {
                    "id": r["id"], "title": r["title"], "status": r["status"],
                    "target_fixed": r["target_fixed"], "students_total": 0, "students_started": 0,
                    "students_done": 0, "completion_pct": None, "avg_progress_pct": None,
                }
            )
    return units


@router.get("/classes/{class_id}/dashboard")
def class_dashboard(db: sqlite3.Connection = Depends(get_db), cls: sqlite3.Row = Depends(owned_class)):
    overview = db.execute("SELECT * FROM v_class_overview WHERE class_id = ?", (cls["id"],)).fetchone()
    students = db.execute(
        """
        SELECT * FROM v_student_stats WHERE class_id = ?
        ORDER BY error_rate_pct IS NULL, error_rate_pct DESC, nickname
        """,
        (cls["id"],),
    ).fetchall()
    ranking = db.execute(
        """
        SELECT rank_in_class, case_id, category, title, students, thread_count, finding_count
        FROM v_class_case_rank WHERE class_id = ? ORDER BY rank_in_class, case_id
        """,
        (cls["id"],),
    ).fetchall()
    return {
        "class": _overview(overview),
        "students": [_student_row(r) for r in students],
        "error_ranking": [
            {
                "rank": r["rank_in_class"], "case_id": r["case_id"], "category": r["category"],
                "title": r["title"], "students": r["students"], "threads": r["thread_count"],
                "findings": r["finding_count"],
            }
            for r in ranking
        ],
        "units": _units(db, cls["id"]),
    }


@router.get("/classes/{class_id}/students/{student_id}")
def student_detail(
    student_id: int,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    stats = db.execute(
        "SELECT * FROM v_student_stats WHERE class_id = ? AND student_id = ?", (cls["id"], student_id)
    ).fetchone()
    if stats is None:
        raise HTTPException(status_code=404, detail="학생을 찾을 수 없습니다.")
    ranking = db.execute(
        """
        SELECT rank_in_student, case_id, category, title, thread_count, finding_count, share_pct
        FROM v_student_case_rank WHERE class_id = ? AND student_id = ?
        ORDER BY rank_in_student, case_id
        """,
        (cls["id"], student_id),
    ).fetchall()
    progress = db.execute(
        """
        SELECT unit_id, unit_title, unit_status, target_fixed, started_threads, fixed_threads, progress_pct, is_done
        FROM v_student_unit_progress WHERE class_id = ? AND student_id = ?
        ORDER BY unit_position, unit_id
        """,
        (cls["id"], student_id),
    ).fetchall()
    return {
        "student": _student_row(stats),
        "error_ranking": [
            {
                "rank": r["rank_in_student"], "case_id": r["case_id"], "category": r["category"],
                "title": r["title"], "threads": r["thread_count"], "findings": r["finding_count"],
                "share_pct": r["share_pct"],
            }
            for r in ranking
        ],
        "progress": [
            {
                "unit_id": r["unit_id"], "title": r["unit_title"], "status": r["unit_status"],
                "target_fixed": r["target_fixed"], "started_threads": r["started_threads"],
                "fixed_threads": r["fixed_threads"], "progress_pct": r["progress_pct"],
                "is_done": bool(r["is_done"]),
            }
            for r in progress
        ],
    }


# ------------------------------------------------------------------ 단원·진도 관리

def _unit_of(db: sqlite3.Connection, class_id: int, unit_id: int) -> sqlite3.Row:
    row = db.execute("SELECT id FROM units WHERE id = ? AND class_id = ?", (unit_id, class_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="단원을 찾을 수 없습니다.")
    return row


@router.get("/classes/{class_id}/units/{unit_id}/students")
def unit_students(
    unit_id: int,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    _unit_of(db, cls["id"], unit_id)
    rows = db.execute(
        """
        SELECT student_id, nickname, started_threads, fixed_threads, progress_pct, is_done, target_fixed
        FROM v_student_unit_progress WHERE class_id = ? AND unit_id = ?
        ORDER BY progress_pct, nickname
        """,
        (cls["id"], unit_id),
    ).fetchall()
    return {
        "students": [
            {
                "id": r["student_id"], "nickname": r["nickname"], "started_threads": r["started_threads"],
                "fixed_threads": r["fixed_threads"], "target_fixed": r["target_fixed"],
                "progress_pct": r["progress_pct"], "is_done": bool(r["is_done"]),
            }
            for r in rows
        ]
    }


@router.post("/classes/{class_id}/units", status_code=201)
def create_unit(
    body: NewUnit,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    try:
        with db:
            position = db.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM units WHERE class_id = ?", (cls["id"],)
            ).fetchone()[0]
            first = cls["current_unit_id"] is None
            cur = db.execute(
                "INSERT INTO units (class_id, title, position, target_fixed, status) VALUES (?, ?, ?, ?, ?)",
                (cls["id"], body.title, position, body.target_fixed, "active" if first else "planned"),
            )
            # 첫 단원은 바로 현재 단원이 되어, 학생이 코드를 올리면 그 단원에 묶인다.
            if first:
                db.execute("UPDATE classes SET current_unit_id = ? WHERE id = ?", (cur.lastrowid, cls["id"]))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="같은 이름의 단원이 이미 있습니다.") from None
    return {"id": cur.lastrowid, "title": body.title, "is_current": first}


@router.patch("/classes/{class_id}/units/{unit_id}")
def update_unit(
    unit_id: int,
    body: UnitUpdate,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    _unit_of(db, cls["id"], unit_id)
    if body.status is None and body.target_fixed is None:
        raise HTTPException(status_code=422, detail="바꿀 값이 없습니다.")
    with db:
        if body.status is not None:
            db.execute("UPDATE units SET status = ? WHERE id = ?", (body.status, unit_id))
        if body.target_fixed is not None:
            db.execute("UPDATE units SET target_fixed = ? WHERE id = ?", (body.target_fixed, unit_id))
    return {"ok": True}


@router.put("/classes/{class_id}/current-unit")
def set_current_unit(
    body: CurrentUnit,
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(owned_class),
):
    if body.unit_id is not None:
        _unit_of(db, cls["id"], body.unit_id)
    with db:
        db.execute("UPDATE classes SET current_unit_id = ? WHERE id = ?", (body.unit_id, cls["id"]))
        # 새로 나가는 단원이 아직 '예정'이면 '진행 중'으로 바꾼다.
        if body.unit_id is not None:
            db.execute("UPDATE units SET status = 'active' WHERE id = ? AND status = 'planned'", (body.unit_id,))
    return {"current_unit_id": body.unit_id}
