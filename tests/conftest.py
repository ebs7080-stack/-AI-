from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.main import app


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
def join_code(client):
    return client.post("/api/classes", json={"name": "1학년 3반"}).json()["join_code"]


@pytest.fixture
def make_student(client, join_code):
    """닉네임을 주면 그 학생으로 입장한 새 브라우저(클라이언트)를 만든다. join_code 를 주면 다른 학급이다."""
    with ExitStack() as stack:

        def make(nickname, code=None):
            c = stack.enter_context(TestClient(app))
            r = c.post("/api/join", json={"join_code": code or join_code, "nickname": nickname})
            assert r.status_code == 200
            return c

        yield make


@pytest.fixture
def student(client, join_code):
    """로그인이 끝난 학생 클라이언트."""
    r = client.post("/api/join", json={"join_code": join_code, "nickname": "철수"})
    assert r.status_code == 200
    return client
