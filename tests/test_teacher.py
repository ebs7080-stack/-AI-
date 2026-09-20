from contextlib import ExitStack
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import app
from app.stats import KST

CODE = "for i in range(3):\nprint(i)\n"
OTHER_CODE = "total = 0\nfor n in range(5):\n    total += n\n"


@pytest.fixture
def classroom(client):
    """학급 코드와 교사 키가 든 새 학급."""
    r = client.post("/api/classes", json={"name": "2학년 1반"})
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def make_teacher(client):
    """학급 정보를 주면 그 학급의 교사로 로그인한 새 브라우저를 만든다."""
    with ExitStack() as stack:

        def make(cls):
            c = stack.enter_context(TestClient(app))
            r = c.post(
                "/api/teacher/login",
                json={"join_code": cls["join_code"], "teacher_key": cls["teacher_key"]},
            )
            assert r.status_code == 200, r.text
            return c

        yield make


@pytest.fixture
def teacher(classroom, make_teacher):
    return make_teacher(classroom)


@pytest.fixture
def join(classroom, make_student):
    """이 학급의 학생 브라우저를 만든다."""
    return lambda nickname: make_student(nickname, code=classroom["join_code"])


def submit(client, code=CODE, thread_id=None):
    body = {"code": code}
    if thread_id is not None:
        body["thread_id"] = thread_id
    r = client.post("/api/submissions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def finish(client, thread_id):
    assert client.post(f"/api/threads/{thread_id}/finish").status_code == 200


def backdate(nickname, days):
    """그 학생의 제출과 스레드를 며칠 전에 만든 것으로 바꾼다."""
    conn = get_connection()
    with conn:
        for table in ("submissions", "threads"):
            conn.execute(
                f"UPDATE {table} SET created_at = datetime('now', ?) "
                "WHERE student_id = (SELECT id FROM students WHERE nickname = ?)",
                (f"-{days} days", nickname),
            )
    conn.close()


def dashboard(client, **params):
    r = client.get("/api/teacher/dashboard", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 로그인 ----------


def test_login_and_me(classroom, make_teacher):
    t = make_teacher(classroom)
    assert t.get("/api/teacher/me").json() == {
        "class_name": "2학년 1반",
        "join_code": classroom["join_code"],
    }


def test_login_accepts_lowercase_code(client, classroom):
    r = client.post(
        "/api/teacher/login",
        json={"join_code": classroom["join_code"].lower(), "teacher_key": classroom["teacher_key"]},
    )
    assert r.status_code == 200


def test_login_rejects_wrong_key_and_unknown_code_the_same_way(client, classroom):
    wrong_key = client.post(
        "/api/teacher/login", json={"join_code": classroom["join_code"], "teacher_key": "nope"}
    )
    unknown = client.post(
        "/api/teacher/login", json={"join_code": "ZZZZZZ", "teacher_key": classroom["teacher_key"]}
    )
    assert wrong_key.status_code == unknown.status_code == 401
    assert wrong_key.json() == unknown.json()  # 어느 쪽이 틀렸는지 알려 주지 않는다.


def test_key_of_another_class_does_not_open_this_class(client, classroom):
    other = client.post("/api/classes", json={"name": "다른 반"}).json()
    r = client.post(
        "/api/teacher/login",
        json={"join_code": classroom["join_code"], "teacher_key": other["teacher_key"]},
    )
    assert r.status_code == 401


def test_dashboard_requires_teacher_login(client):
    assert client.get("/api/teacher/me").status_code == 401
    assert client.get("/api/teacher/dashboard").status_code == 401


def test_student_session_is_not_a_teacher_session(student):
    assert student.get("/api/teacher/dashboard").status_code == 401


def test_teacher_session_is_not_a_student_session(teacher):
    assert teacher.get("/api/me").status_code == 401
    assert teacher.post("/api/submissions", json={"code": CODE}).status_code == 401


def test_logout_ends_session(teacher):
    assert teacher.post("/api/teacher/logout").status_code == 204
    assert teacher.get("/api/teacher/me").status_code == 401


def test_expired_session_is_rejected(teacher):
    conn = get_connection()
    with conn:
        conn.execute("UPDATE teacher_sessions SET created_at = datetime('now', '-13 hours')")
    conn.close()
    assert teacher.get("/api/teacher/me").status_code == 401


def test_days_must_be_in_range(teacher):
    for days in (0, -1, 366):
        assert teacher.get("/api/teacher/dashboard", params={"days": days}).status_code == 422


# ---------- 통계 ----------


def test_empty_class(teacher):
    data = dashboard(teacher, days=7)
    assert data["class"]["name"] == "2학년 1반"
    assert data["summary"]["students_total"] == 0
    assert data["summary"]["submissions"] == 0
    assert data["summary"]["median_attempts"] is None
    assert data["summary"]["median_fix_minutes"] is None
    assert data["errors"] == []
    assert data["students"] == []
    # 제출이 없어도 최근 7일이 0으로 채워져 나온다.
    assert len(data["daily"]) == 7
    assert all(day["submissions"] == 0 for day in data["daily"])
    assert dashboard(teacher)["daily"] == []  # 전체 기간인데 데이터가 없으면 빈 목록


def test_summary_errors_daily_and_students(teacher, join):
    minsu = join("민수")
    yeonghui = join("영희")

    first = submit(minsu)
    submit(minsu, OTHER_CODE, thread_id=first["thread_id"])
    finish(minsu, first["thread_id"])  # 민수: 두 번 만에 다 고침
    submit(yeonghui)  # 영희: 아직 고치는 중

    data = dashboard(teacher, days=7)
    summary = data["summary"]
    assert summary["students_total"] == 2
    assert summary["students_active"] == 2
    assert summary["submissions"] == 3
    assert summary["threads"] == {"open": 1, "fixed": 1, "dropped": 0}
    assert summary["median_attempts"] == 2
    assert summary["median_fix_minutes"] >= 0
    assert summary["help_sessions"] == 0

    # 임시 분석기는 제출마다 같은 case 를 내므로 두 학생, 두 코드가 만났다고 센다.
    assert data["errors"] == [
        {"case_id": "stub", "label": "임시 분석 결과 (분석기 연결 전)", "students": 2, "threads": 2}
    ]

    today = datetime.now(timezone.utc).astimezone(KST).date().isoformat()
    assert data["daily"][-1] == {"date": today, "submissions": 3, "students": 2}

    # 고치는 중인 학생이 맨 위에 온다.
    assert [s["nickname"] for s in data["students"]] == ["영희", "민수"]
    fixing, idle = data["students"]
    assert fixing["state"] == "fixing" and fixing["current_attempts"] == 1
    assert fixing["current_minutes"] >= 0
    assert idle["state"] == "idle" and idle["current_attempts"] is None
    assert idle["submissions"] == 2 and idle["fixed"] == 1 and idle["dropped"] == 0
    assert idle["last_submission_at"].endswith("+00:00")


def test_starting_a_new_code_counts_the_old_one_as_dropped(teacher, join):
    minsu = join("민수")
    submit(minsu)
    submit(minsu, OTHER_CODE)  # thread_id 없이 다시 제출하면 고치던 코드는 그만둔 것이다.

    data = dashboard(teacher)
    assert data["summary"]["threads"] == {"open": 1, "fixed": 0, "dropped": 1}
    assert data["students"][0]["dropped"] == 1


def test_helping_state_and_counts(teacher, join):
    helper = join("영희")
    owner = join("민수")
    finish(helper, submit(helper)["thread_id"])
    submit(owner)
    assert helper.post("/api/help/start").status_code == 200

    data = dashboard(teacher, days=7)
    assert data["summary"]["help_sessions"] == 1
    by_name = {s["nickname"]: s for s in data["students"]}
    assert by_name["영희"]["state"] == "helping" and by_name["영희"]["helped"] == 1
    assert by_name["민수"]["state"] == "fixing" and by_name["민수"]["was_helped"] == 1
    assert [s["nickname"] for s in data["students"]] == ["민수", "영희"]


def test_period_filter(teacher, join):
    minsu = join("민수")
    yeonghui = join("영희")
    submit(minsu)
    backdate("민수", 40)
    submit(yeonghui)

    week = dashboard(teacher, days=7)
    assert week["summary"]["submissions"] == 1
    assert week["summary"]["students_active"] == 1
    assert week["summary"]["threads"]["open"] == 1
    assert week["errors"][0]["students"] == 1
    assert len(week["daily"]) == 7
    by_name = {s["nickname"]: s for s in week["students"]}
    assert by_name["민수"]["submissions"] == 0  # 기간 밖 제출은 세지 않는다.
    assert by_name["민수"]["last_submission_at"] is not None

    everything = dashboard(teacher)
    assert everything["summary"]["submissions"] == 2
    assert everything["errors"][0]["students"] == 2
    assert len(everything["daily"]) >= 41  # 가장 오래된 제출 날부터 오늘까지 이어진다.
    assert everything["daily"][0]["submissions"] == 1
    assert everything["daily"][-1]["submissions"] == 1


def test_each_teacher_only_sees_their_own_class(teacher, join, make_teacher, make_student, client):
    submit(join("민수"))

    other = client.post("/api/classes", json={"name": "3학년 2반"}).json()
    outsider = make_student("지수", code=other["join_code"])
    submit(outsider, OTHER_CODE)
    submit(outsider, "x = 1\n")

    mine = dashboard(teacher)
    assert mine["class"]["name"] == "2학년 1반"
    assert [s["nickname"] for s in mine["students"]] == ["민수"]
    assert mine["summary"]["submissions"] == 1

    theirs = dashboard(make_teacher(other))
    assert [s["nickname"] for s in theirs["students"]] == ["지수"]
    assert theirs["summary"]["submissions"] == 2


def test_dashboard_never_exposes_code_or_hints(teacher, join):
    minsu = join("민수")
    submit(minsu, "secret_variable = 12345\nprint(secret_variable)\n")

    text = teacher.get("/api/teacher/dashboard").text
    assert "secret_variable" not in text
    assert "12345" not in text
    assert "종이에 적어" not in text  # 힌트 본문
