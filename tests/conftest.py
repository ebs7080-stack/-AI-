from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "correct-horse-1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def other_client(client):
    """같은 DB를 쓰지만 쿠키가 따로인 두 번째 브라우저."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_teacher(client):
    """아이디를 주면 그 아이디로 가입해 로그인한 교사 브라우저를 만든다."""
    with ExitStack() as stack:

        def make(username, name="김선생"):
            c = stack.enter_context(TestClient(app))
            r = c.post(
                "/api/teacher/signup",
                json={"username": username, "password": PASSWORD, "name": name},
            )
            assert r.status_code == 201, r.text
            return c

        yield make


@pytest.fixture
def teacher(make_teacher):
    """로그인이 끝난 교사 클라이언트. 학생 브라우저(client)와는 쿠키가 따로다."""
    return make_teacher("teacher1")


@pytest.fixture
def make_class(teacher):
    """교실을 만들어 {id, name, join_code, ...} 를 돌려준다. by 를 주면 그 교사의 교실이다."""

    def make(name, by=None, students=()):
        r = (by or teacher).post(
            "/api/teacher/classes",
            json={"name": name, "students": [{"student_no": no} for no in students]},
        )
        assert r.status_code == 201, r.text
        return r.json()

    return make


@pytest.fixture
def classroom(make_class):
    return make_class("1학년 3반")


@pytest.fixture
def join_code(classroom):
    return classroom["join_code"]


@pytest.fixture
def open_help_time(teacher, classroom):
    """교실의 도움 시간을 연다 (친구 돕기와 채팅은 이 시간에만 된다). 다시 부르면 그만큼 늘어난다."""

    def open_(minutes=5, cls=None, by=None):
        r = (by or teacher).post(
            f"/api/teacher/classes/{(cls or classroom)['id']}/help-time", json={"minutes": minutes}
        )
        assert r.status_code == 200, r.text
        return r.json()

    return open_


@pytest.fixture
def enroll(teacher, classroom):
    """학번을 교실 명단에 등록한다. cls 와 by 를 주면 다른 교사의 다른 교실이다."""

    def add(student_no, cls=None, by=None):
        cls = cls or classroom
        r = (by or teacher).post(
            f"/api/teacher/classes/{cls['id']}/students",
            json={"students": [{"student_no": student_no}]},
        )
        assert r.status_code == 200, r.text
        return cls["join_code"]

    return add


@pytest.fixture
def make_student(enroll):
    """학번을 주면 명단에 등록하고, 그 학생으로 입장한 새 브라우저(클라이언트)를 만든다.

    이름을 따로 주지 않으므로 화면에 보이는 이름도 학번과 같다.
    """
    with ExitStack() as stack:

        def make(student_no, cls=None, by=None):
            code = enroll(student_no, cls, by)
            c = stack.enter_context(TestClient(app))
            r = c.post("/api/join", json={"join_code": code, "student_no": student_no})
            assert r.status_code == 200, r.text
            return c

        yield make


@pytest.fixture
def student(client, enroll):
    """로그인이 끝난 학생 클라이언트."""
    code = enroll("철수")
    r = client.post("/api/join", json={"join_code": code, "student_no": "철수"})
    assert r.status_code == 200
    return client
