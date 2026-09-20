import hashlib
import secrets
import sqlite3

from fastapi import Cookie, Depends, HTTPException

from .db import get_db

SESSION_COOKIE = "cwa_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30일

# 교사는 학교 공용 PC 에서 쓸 수 있고 학생 기록을 보므로 세션을 짧게 두고 서버에서도 만료를 확인한다.
TEACHER_COOKIE = "cwa_teacher"
TEACHER_COOKIE_PATH = "/api/teacher"  # 교사 API 에만 쿠키가 나간다.
TEACHER_SESSION_MAX_AGE = 60 * 60 * 12  # 12시간


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def student_for_token(db: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """세션 토큰으로 학생을 찾는다. HTTP 의존성과 WebSocket 이 함께 쓴다."""
    if not token:
        return None
    return db.execute(
        """
        SELECT st.id, st.nickname, st.class_id, c.name AS class_name
        FROM sessions se
        JOIN students st ON st.id = se.student_id
        JOIN classes c ON c.id = st.class_id
        WHERE se.token_hash = ?
        """,
        (hash_token(token),),
    ).fetchone()


def current_student(
    db: sqlite3.Connection = Depends(get_db),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> sqlite3.Row:
    row = student_for_token(db, session)
    if row is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return row


def teacher_key_matches(cls: sqlite3.Row | None, teacher_key: str) -> bool:
    """학급이 있고 교사 키가 맞을 때만 True. 학급이 없어도 같은 계산을 해서 응답 시간 차이를 줄인다."""
    stored = cls["teacher_key_hash"] if cls is not None else ""
    matches = secrets.compare_digest(stored, hash_token(teacher_key))
    return cls is not None and matches


def class_for_teacher_token(db: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """교사 세션 토큰으로 학급을 찾는다. 만료된 세션은 없는 것으로 본다."""
    if not token:
        return None
    return db.execute(
        """
        SELECT c.id, c.name, c.join_code
        FROM teacher_sessions ts
        JOIN classes c ON c.id = ts.class_id
        WHERE ts.token_hash = ? AND ts.created_at >= datetime('now', ?)
        """,
        (hash_token(token), f"-{TEACHER_SESSION_MAX_AGE} seconds"),
    ).fetchone()


def current_class(
    db: sqlite3.Connection = Depends(get_db),
    session: str | None = Cookie(default=None, alias=TEACHER_COOKIE),
) -> sqlite3.Row:
    """교사 로그인 확인. 교사 API 는 이 학급의 데이터만 다룬다 (다른 학급은 접근 경로 자체가 없다)."""
    row = class_for_teacher_token(db, session)
    if row is None:
        raise HTTPException(status_code=401, detail="교사 로그인이 필요합니다.")
    return row
