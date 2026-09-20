"""교사 로그인과 학급 대시보드 (FR-3).

교사는 학급을 만들 때 받은 학급 코드 + 교사 키로 로그인하고, 그 학급의 통계만 본다.
학생 세션(cwa_session)과는 쿠키가 따로라서 학생이 교사 API 를 열 수 없다.
"""

import sqlite3

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .auth import (
    TEACHER_COOKIE,
    TEACHER_COOKIE_PATH,
    TEACHER_SESSION_MAX_AGE,
    current_class,
    hash_token,
    new_token,
    teacher_key_matches,
)
from .db import get_db
from .stats import class_dashboard

router = APIRouter(prefix="/api/teacher")


class TeacherLogin(BaseModel):
    join_code: str = Field(min_length=1, max_length=20)
    teacher_key: str = Field(min_length=1, max_length=100)


@router.post("/login")
def login(body: TeacherLogin, response: Response, db: sqlite3.Connection = Depends(get_db)):
    cls = db.execute(
        "SELECT id, name, join_code, teacher_key_hash FROM classes WHERE join_code = ?",
        (body.join_code.strip().upper(),),
    ).fetchone()
    # 학급 코드가 틀린 경우와 교사 키가 틀린 경우를 구별해 알려 주지 않는다.
    if not teacher_key_matches(cls, body.teacher_key.strip()):
        raise HTTPException(status_code=401, detail="학급 코드 또는 교사 키가 맞지 않습니다.")

    token = new_token()
    with db:
        db.execute(
            "DELETE FROM teacher_sessions WHERE created_at < datetime('now', ?)",
            (f"-{TEACHER_SESSION_MAX_AGE} seconds",),
        )
        db.execute(
            "INSERT INTO teacher_sessions (token_hash, class_id) VALUES (?, ?)",
            (hash_token(token), cls["id"]),
        )
    response.set_cookie(
        TEACHER_COOKIE,
        token,
        max_age=TEACHER_SESSION_MAX_AGE,
        path=TEACHER_COOKIE_PATH,
        httponly=True,
        samesite="lax",
    )
    return {"class_name": cls["name"], "join_code": cls["join_code"]}


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
def me(cls: sqlite3.Row = Depends(current_class)):
    return {"class_name": cls["name"], "join_code": cls["join_code"]}


@router.get("/dashboard")
def dashboard(
    # 최근 며칠(오늘 포함). 없으면 전체 기간.
    days: int | None = Query(default=None, ge=1, le=365),
    db: sqlite3.Connection = Depends(get_db),
    cls: sqlite3.Row = Depends(current_class),
):
    return {
        "class": {"name": cls["name"], "join_code": cls["join_code"]},
        **class_dashboard(db, cls["id"], days),
    }
