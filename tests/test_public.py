"""인터넷에 공개해서 쓸 때(start_public.bat)의 보호 장치: 초대 코드, HTTPS 전용 쿠키, 실패 횟수 제한."""

from app import ratelimit
from tests.conftest import PASSWORD

SIGNUP = {"username": "teacher9", "password": PASSWORD, "name": "김선생"}


def cookie_header(response):
    return response.headers["set-cookie"].lower()


# ---------- 초대 코드 ----------


def test_signup_is_open_without_an_invite_code_setting(client):
    assert client.get("/api/teacher/signup-info").json() == {"invite_required": False}
    assert client.post("/api/teacher/signup", json=SIGNUP).status_code == 201


def test_signup_requires_the_invite_code_when_set(client, monkeypatch):
    monkeypatch.setenv("CWA_SIGNUP_CODE", "open-sesame")
    info = client.get("/api/teacher/signup-info")
    assert info.json() == {"invite_required": True} and "open-sesame" not in info.text

    assert client.post("/api/teacher/signup", json=SIGNUP).status_code == 403
    wrong = client.post("/api/teacher/signup", json={**SIGNUP, "invite_code": "guess"})
    assert wrong.status_code == 403 and "초대 코드" in wrong.json()["detail"]
    assert client.get("/api/teacher/me").status_code == 401

    ok = client.post("/api/teacher/signup", json={**SIGNUP, "invite_code": " open-sesame "})
    assert ok.status_code == 201
    assert client.get("/api/teacher/me").json()["username"] == "teacher9"


def test_existing_teachers_log_in_without_the_invite_code(teacher, other_client, monkeypatch):
    monkeypatch.setenv("CWA_SIGNUP_CODE", "open-sesame")
    r = other_client.post("/api/teacher/login", json={"username": "teacher1", "password": PASSWORD})
    assert r.status_code == 200


# ---------- 쿠키 ----------


def test_cookies_are_not_https_only_on_the_local_pc(teacher, classroom, enroll, other_client, client):
    # http://127.0.0.1 에서 Secure 쿠키를 쓰면 로그인이 유지되지 않는다.
    login = other_client.post("/api/teacher/login", json={"username": "teacher1", "password": PASSWORD})
    assert "secure" not in cookie_header(login)
    join = client.post("/api/join", json={"join_code": enroll("10301"), "student_no": "10301"})
    assert "secure" not in cookie_header(join)


def test_cookies_are_https_only_in_public_mode(teacher, enroll, other_client, client, monkeypatch):
    code = enroll("10301")
    monkeypatch.setenv("CWA_PUBLIC", "1")
    login = other_client.post("/api/teacher/login", json={"username": "teacher1", "password": PASSWORD})
    assert "secure" in cookie_header(login) and "httponly" in cookie_header(login)
    join = client.post("/api/join", json={"join_code": code, "student_no": "10301"})
    assert "secure" in cookie_header(join) and "httponly" in cookie_header(join)


# ---------- 실패 횟수 제한 ----------


def test_too_many_failed_teacher_logins_are_blocked(teacher, client):
    wrong = {"username": "teacher1", "password": "wrong-password"}
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post("/api/teacher/login", json=wrong).status_code == 401
    blocked = client.post("/api/teacher/login", json=wrong)
    assert blocked.status_code == 429 and "잠시" in blocked.json()["detail"]
    # 막힌 동안에는 맞는 비밀번호도 받지 않는다 (계속 넣어 보다 맞히는 것을 막는다).
    right = {"username": "teacher1", "password": PASSWORD}
    assert client.post("/api/teacher/login", json=right).status_code == 429
    # 이미 로그인한 사람은 계속 쓸 수 있다.
    assert teacher.get("/api/teacher/classes").status_code == 200


def test_too_many_failed_joins_are_blocked(client, enroll):
    code = enroll("10301")
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post("/api/join", json={"join_code": "ZZZZZZ", "student_no": "1"}).status_code == 401
    assert client.post("/api/join", json={"join_code": code, "student_no": "10301"}).status_code == 429


def test_block_ends_after_the_window(teacher, client, monkeypatch):
    wrong = {"username": "teacher1", "password": "wrong-password"}
    for _ in range(ratelimit.MAX_FAILURES):
        client.post("/api/teacher/login", json=wrong)
    assert client.post("/api/teacher/login", json=wrong).status_code == 429

    later = ratelimit.time.monotonic() + ratelimit.WINDOW_SECONDS + 1
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: later)
    right = {"username": "teacher1", "password": PASSWORD}
    assert client.post("/api/teacher/login", json=right).status_code == 200


def test_a_few_typos_do_not_block(client, enroll):
    code = enroll("10301")
    for _ in range(5):
        client.post("/api/join", json={"join_code": code, "student_no": "오타"})
    assert client.post("/api/join", json={"join_code": code, "student_no": "10301"}).status_code == 200
