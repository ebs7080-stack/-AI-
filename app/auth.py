import hashlib
import os
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

# scrypt 비용. 로그인 한 번에 수십 ms 가 걸려 비밀번호 무차별 대입을 느리게 만든다.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def secure_cookies() -> bool:
    """인터넷에 공개해서 HTTPS 로 접속할 때(CWA_PUBLIC=1)는 쿠키가 HTTPS 로만 나가게 한다.

    내 PC 에서 http://127.0.0.1 로 쓸 때 켜면 브라우저가 쿠키를 보내지 않아 로그인이 안 되므로 기본은 꺼 둔다.
    """
    return os.environ.get("CWA_PUBLIC") == "1"


def signup_code() -> str:
    """교사 회원 가입에 필요한 초대 코드. 비어 있으면 누구나 가입할 수 있다 (CWA_SIGNUP_CODE)."""
    return os.environ.get("CWA_SIGNUP_CODE", "").strip()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str, salt: bytes | None = None) -> str:
    """비밀번호를 '솔트$해시'(16진수)로 만든다. 세션 토큰과 달리 사람이 고른 값이라 느린 해시를 쓴다."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"{salt.hex()}${digest.hex()}"


# 없는 아이디로 로그인해도 같은 계산을 하도록 견줄 해시를 하나 둔다.
_NO_ACCOUNT_HASH = hash_password(secrets.token_urlsafe(16))


def password_matches(teacher: sqlite3.Row | None, password: str) -> bool:
    """계정이 있고 비밀번호가 맞을 때만 True. 계정이 없어도 같은 계산을 해서 응답 시간 차이를 줄인다."""
    stored = teacher["password_hash"] if teacher is not None else _NO_ACCOUNT_HASH
    salt = bytes.fromhex(stored.split("$", 1)[0])
    matches = secrets.compare_digest(stored, hash_password(password, salt))
    return teacher is not None and matches


def student_for_token(db: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """세션 토큰으로 학생을 찾는다. HTTP 의존성과 WebSocket 이 함께 쓴다."""
    if not token:
        return None
    return db.execute(
        """
        SELECT st.id, st.student_no, st.name, st.class_id, c.name AS class_name
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


def teacher_for_token(db: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """교사 세션 토큰으로 교사를 찾는다. 만료된 세션은 없는 것으로 본다."""
    if not token:
        return None
    return db.execute(
        """
        SELECT t.id, t.username, t.name
        FROM teacher_sessions ts
        JOIN teachers t ON t.id = ts.teacher_id
        WHERE ts.token_hash = ? AND ts.created_at >= datetime('now', ?)
        """,
        (hash_token(token), f"-{TEACHER_SESSION_MAX_AGE} seconds"),
    ).fetchone()


def current_teacher(
    db: sqlite3.Connection = Depends(get_db),
    session: str | None = Cookie(default=None, alias=TEACHER_COOKIE),
) -> sqlite3.Row:
    row = teacher_for_token(db, session)
    if row is None:
        raise HTTPException(status_code=401, detail="교사 로그인이 필요합니다.")
    return row


def owned_class(
    class_id: int,
    db: sqlite3.Connection = Depends(get_db),
    teacher: sqlite3.Row = Depends(current_teacher),
) -> sqlite3.Row:
    """경로의 교실이 로그인한 교사의 것인지 확인한다. 남의 교실은 존재 여부도 숨기려고 404 로 응답한다."""
    row = db.execute(
        "SELECT id, name, join_code, hide_names, current_unit_id FROM classes WHERE id = ? AND teacher_id = ?",
        (class_id, teacher["id"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="교실을 찾을 수 없습니다.")
    return row
