from datetime import datetime, timezone

import pytest

from app.db import get_connection
from app.stats import KST
from tests.conftest import PASSWORD

CODE = "for i in range(3):\nprint(i)\n"
OTHER_CODE = "total = 0\nfor n in range(5):\n    total += n\n"


@pytest.fixture
def classroom(make_class):
    """이 파일의 테스트가 쓰는 교실 (conftest 의 classroom 을 바꿔 끼운다)."""
    return make_class("2학년 1반")


def submit(client, code=CODE, thread_id=None):
    body = {"code": code}
    if thread_id is not None:
        body["thread_id"] = thread_id
    r = client.post("/api/submissions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def finish(client, thread_id):
    assert client.post(f"/api/threads/{thread_id}/finish").status_code == 200


def backdate(student_no, days):
    """그 학생의 제출과 스레드를 며칠 전에 만든 것으로 바꾼다."""
    conn = get_connection()
    with conn:
        for table in ("submissions", "threads"):
            conn.execute(
                f"UPDATE {table} SET created_at = datetime('now', ?) "
                "WHERE student_id = (SELECT id FROM students WHERE student_no = ?)",
                (f"-{days} days", student_no),
            )
    conn.close()


def dashboard(client, cls, **params):
    r = client.get(f"/api/teacher/classes/{cls['id']}/dashboard", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def roster(client, cls):
    r = client.get(f"/api/teacher/classes/{cls['id']}")
    assert r.status_code == 200, r.text
    return r.json()["students"]


# ---------- 회원 가입과 로그인 ----------


def test_signup_logs_in(teacher):
    assert teacher.get("/api/teacher/me").json() == {"username": "teacher1", "name": "김선생"}


def test_login_after_signup(teacher, other_client):
    # 아이디는 대소문자를 가리지 않는다.
    r = other_client.post("/api/teacher/login", json={"username": "Teacher1", "password": PASSWORD})
    assert r.status_code == 200
    assert other_client.get("/api/teacher/me").json()["username"] == "teacher1"


def test_signup_rejects_taken_username(teacher, other_client):
    r = other_client.post(
        "/api/teacher/signup", json={"username": "TEACHER1", "password": PASSWORD, "name": "다른 사람"}
    )
    assert r.status_code == 409
    assert other_client.get("/api/teacher/me").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"username": "abc", "password": PASSWORD, "name": "김선생"},  # 아이디가 짧다
        {"username": "한글아이디", "password": PASSWORD, "name": "김선생"},
        {"username": "teacher2", "password": "short", "name": "김선생"},
        {"username": "teacher2", "password": PASSWORD, "name": "   "},
    ],
)
def test_signup_validates_input(client, body):
    assert client.post("/api/teacher/signup", json=body).status_code == 422


def test_login_rejects_wrong_password_and_unknown_username_the_same_way(teacher, client):
    wrong = client.post("/api/teacher/login", json={"username": "teacher1", "password": "wrong-password"})
    unknown = client.post("/api/teacher/login", json={"username": "nobody", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # 어느 쪽이 틀렸는지 알려 주지 않는다.


def test_password_is_stored_only_as_a_hash(teacher):
    conn = get_connection()
    stored = conn.execute("SELECT password_hash FROM teachers").fetchone()["password_hash"]
    conn.close()
    assert PASSWORD not in stored


def test_teacher_api_requires_login(client, classroom):
    cid = classroom["id"]
    assert client.get("/api/teacher/me").status_code == 401
    assert client.get("/api/teacher/classes").status_code == 401
    assert client.get(f"/api/teacher/classes/{cid}").status_code == 401
    assert client.get(f"/api/teacher/classes/{cid}/dashboard").status_code == 401
    r = client.post(f"/api/teacher/classes/{cid}/students", json={"students": [{"student_no": "1"}]})
    assert r.status_code == 401


def test_student_session_is_not_a_teacher_session(student, classroom):
    assert student.get("/api/teacher/classes").status_code == 401
    assert student.get(f"/api/teacher/classes/{classroom['id']}/dashboard").status_code == 401


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


def test_change_password(teacher, other_client, client):
    # 같은 계정으로 다른 기기에서도 로그인해 둔다.
    other_client.post("/api/teacher/login", json={"username": "teacher1", "password": PASSWORD})
    new = "brand-new-password-2"

    r = teacher.post("/api/teacher/password", json={"current_password": PASSWORD, "new_password": new})
    assert r.status_code == 204
    assert teacher.get("/api/teacher/me").status_code == 200  # 바꾼 브라우저는 그대로 로그인돼 있다.
    assert other_client.get("/api/teacher/me").status_code == 401  # 다른 기기의 로그인은 끊긴다.

    old = client.post("/api/teacher/login", json={"username": "teacher1", "password": PASSWORD})
    assert old.status_code == 401
    assert client.post("/api/teacher/login", json={"username": "teacher1", "password": new}).status_code == 200


def test_change_password_requires_the_current_one(teacher, client):
    url = "/api/teacher/password"
    new = "brand-new-password-2"
    assert client.post(url, json={"current_password": PASSWORD, "new_password": new}).status_code == 401
    assert teacher.post(url, json={"current_password": "wrong-password", "new_password": new}).status_code == 403
    assert teacher.post(url, json={"current_password": PASSWORD, "new_password": "short"}).status_code == 422
    assert teacher.post(url, json={"current_password": PASSWORD, "new_password": PASSWORD}).status_code == 422
    # 실패한 요청은 비밀번호를 바꾸지 않는다.
    assert client.post("/api/teacher/login", json={"username": "teacher1", "password": PASSWORD}).status_code == 200


# ---------- 교실과 학생 명단 ----------


def test_teacher_can_have_several_classes(teacher, make_class):
    first = make_class("1학년 1반", students=["10101", "10102"])
    second = make_class("1학년 2반")
    assert len(first["join_code"]) == 6 and first["join_code"] != second["join_code"]

    listed = teacher.get("/api/teacher/classes").json()
    assert [(c["name"], c["student_count"]) for c in listed] == [("1학년 2반", 0), ("1학년 1반", 2)]


def test_create_class_rejects_blank_name(teacher):
    assert teacher.post("/api/teacher/classes", json={"name": "   "}).status_code == 422


def test_roster_add_names_and_duplicates(teacher, classroom):
    url = f"/api/teacher/classes/{classroom['id']}/students"
    r = teacher.post(
        url,
        json={"students": [{"student_no": " 20102 ", "name": " 이영희 "}, {"student_no": "20101"}]},
    )
    assert r.status_code == 200 and r.json()["skipped"] == []
    # 학번 순으로 나오고, 이름을 적지 않으면 학번이 이름 자리에 들어간다.
    assert [(s["student_no"], s["name"]) for s in r.json()["students"]] == [
        ("20101", "20101"),
        ("20102", "이영희"),
    ]

    again = teacher.post(url, json={"students": [{"student_no": "20102"}, {"student_no": "20103"}]})
    assert again.json()["skipped"] == ["20102"]
    assert len(roster(teacher, classroom)) == 3
    assert roster(teacher, classroom)[1]["name"] == "이영희"  # 건너뛴 학번의 이름은 그대로다.


def test_same_number_can_be_used_in_different_classes(teacher, make_class, classroom):
    other = make_class("3학년 2반", students=["10101"])
    r = teacher.post(
        f"/api/teacher/classes/{classroom['id']}/students", json={"students": [{"student_no": "10101"}]}
    )
    assert r.json()["skipped"] == []
    assert len(roster(teacher, other)) == 1


def test_roster_rejects_blank_number(teacher, classroom):
    url = f"/api/teacher/classes/{classroom['id']}/students"
    assert teacher.post(url, json={"students": [{"student_no": "  "}]}).status_code == 422
    assert teacher.post(url, json={"students": []}).status_code == 422


def test_removed_student_cannot_log_in(teacher, classroom, make_student):
    minsu = make_student("민수")
    student_id = roster(teacher, classroom)[0]["id"]
    url = f"/api/teacher/classes/{classroom['id']}/students/{student_id}"
    assert teacher.delete(url).status_code == 204
    assert roster(teacher, classroom) == []
    assert minsu.get("/api/me").status_code == 401  # 열려 있던 세션도 끊긴다.
    r = minsu.post("/api/join", json={"join_code": classroom["join_code"], "student_no": "민수"})
    assert r.status_code == 401
    assert teacher.delete(url).status_code == 404


def test_student_with_records_cannot_be_removed(teacher, classroom, make_student):
    submit(make_student("민수"))
    entry = roster(teacher, classroom)[0]
    assert entry["has_records"] is True
    r = teacher.delete(f"/api/teacher/classes/{classroom['id']}/students/{entry['id']}")
    assert r.status_code == 409
    assert len(roster(teacher, classroom)) == 1


def test_other_teachers_class_looks_like_it_does_not_exist(teacher, classroom, make_teacher, make_student):
    make_student("민수")
    student_id = roster(teacher, classroom)[0]["id"]
    stranger = make_teacher("teacher2")
    cid = classroom["id"]

    assert stranger.get("/api/teacher/classes").json() == []
    assert stranger.get(f"/api/teacher/classes/{cid}").status_code == 404
    assert stranger.get(f"/api/teacher/classes/{cid}/dashboard").status_code == 404
    r = stranger.post(f"/api/teacher/classes/{cid}/students", json={"students": [{"student_no": "x"}]})
    assert r.status_code == 404
    assert stranger.delete(f"/api/teacher/classes/{cid}/students/{student_id}").status_code == 404
    assert len(roster(teacher, classroom)) == 1


# ---------- 통계 ----------


def test_days_must_be_in_range(teacher, classroom):
    for days in (0, -1, 366):
        r = teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard", params={"days": days})
        assert r.status_code == 422


def test_empty_class(teacher, classroom):
    data = dashboard(teacher, classroom, days=7)
    assert data["class"] == {
        "id": classroom["id"],
        "name": "2학년 1반",
        "join_code": classroom["join_code"],
        "hide_names": False,
        "current_unit_id": None,  # 단원을 만들기 전에는 지금 단원이 없다.
    }
    assert data["summary"]["students_total"] == 0
    assert data["summary"]["submissions"] == 0
    assert data["summary"]["median_attempts"] is None
    assert data["summary"]["median_fix_minutes"] is None
    assert data["errors"] == []
    assert data["students"] == []
    # 제출이 없어도 최근 7일이 0으로 채워져 나온다.
    assert len(data["daily"]) == 7
    assert all(day["submissions"] == 0 for day in data["daily"])
    assert dashboard(teacher, classroom)["daily"] == []  # 전체 기간인데 데이터가 없으면 빈 목록


def test_registered_student_appears_before_first_login(teacher, classroom, enroll):
    enroll("20101")
    data = dashboard(teacher, classroom)
    assert data["summary"]["students_total"] == 1 and data["summary"]["students_active"] == 0
    assert [(s["student_no"], s["state"], s["last_submission_at"]) for s in data["students"]] == [
        ("20101", "idle", None)
    ]


def test_summary_errors_daily_and_students(teacher, classroom, make_student):
    minsu = make_student("민수")
    yeonghui = make_student("영희")

    first = submit(minsu)
    submit(minsu, OTHER_CODE, thread_id=first["thread_id"])
    finish(minsu, first["thread_id"])  # 민수: 두 번 만에 다 고침
    submit(yeonghui)  # 영희: 아직 고치는 중

    data = dashboard(teacher, classroom, days=7)
    summary = data["summary"]
    assert summary["students_total"] == 2
    assert summary["students_active"] == 2
    assert summary["submissions"] == 3
    assert summary["threads"] == {"open": 1, "fixed": 1, "dropped": 0}
    assert summary["median_attempts"] == 2
    assert summary["median_fix_minutes"] >= 0
    assert summary["help_sessions"] == 0

    # 두 학생이 같은 문법 오류(반복문 본문 들여쓰기)를 한 번씩 만났다. 고친 뒤의 제출에는 오류가 없다.
    assert data["errors"] == [
        {
            "case_id": "loop.body_not_indented",
            "label": "[반복문] 반복할 문장이 반복문 안에 들어가지 않게 들여쓰기 하기",
            "students": 2,
            "threads": 2,
        }
    ]

    today = datetime.now(timezone.utc).astimezone(KST).date().isoformat()
    assert data["daily"][-1] == {"date": today, "submissions": 3, "students": 2}

    # 고치는 중인 학생이 맨 위에 온다.
    assert [s["student_no"] for s in data["students"]] == ["영희", "민수"]
    fixing, idle = data["students"]
    assert fixing["state"] == "fixing" and fixing["current_attempts"] == 1
    assert fixing["current_minutes"] >= 0
    assert idle["state"] == "idle" and idle["current_attempts"] is None
    assert idle["submissions"] == 2 and idle["fixed"] == 1 and idle["dropped"] == 0
    assert idle["last_submission_at"].endswith("+00:00")


def test_starting_a_new_code_counts_the_old_one_as_dropped(teacher, classroom, make_student):
    minsu = make_student("민수")
    submit(minsu)
    submit(minsu, OTHER_CODE)  # thread_id 없이 다시 제출하면 고치던 코드는 그만둔 것이다.

    data = dashboard(teacher, classroom)
    assert data["summary"]["threads"] == {"open": 1, "fixed": 0, "dropped": 1}
    assert data["students"][0]["dropped"] == 1


def test_helping_state_and_counts(teacher, classroom, make_student, open_help_time):
    open_help_time()
    helper = make_student("영희")
    owner = make_student("민수")
    finish(helper, submit(helper)["thread_id"])
    submit(owner)
    assert helper.post("/api/help/start").status_code == 200

    data = dashboard(teacher, classroom, days=7)
    assert data["summary"]["help_sessions"] == 1
    by_no = {s["student_no"]: s for s in data["students"]}
    assert by_no["영희"]["state"] == "helping" and by_no["영희"]["helped"] == 1
    assert by_no["민수"]["state"] == "fixing" and by_no["민수"]["was_helped"] == 1
    assert [s["student_no"] for s in data["students"]] == ["민수", "영희"]


def test_period_filter(teacher, classroom, make_student):
    minsu = make_student("민수")
    yeonghui = make_student("영희")
    submit(minsu)
    backdate("민수", 40)
    submit(yeonghui)

    week = dashboard(teacher, classroom, days=7)
    assert week["summary"]["submissions"] == 1
    assert week["summary"]["students_active"] == 1
    assert week["summary"]["threads"]["open"] == 1
    assert week["errors"][0]["students"] == 1
    assert len(week["daily"]) == 7
    by_no = {s["student_no"]: s for s in week["students"]}
    assert by_no["민수"]["submissions"] == 0  # 기간 밖 제출은 세지 않는다.
    assert by_no["민수"]["last_submission_at"] is not None

    everything = dashboard(teacher, classroom)
    assert everything["summary"]["submissions"] == 2
    assert everything["errors"][0]["students"] == 2
    assert len(everything["daily"]) >= 41  # 가장 오래된 제출 날부터 오늘까지 이어진다.
    assert everything["daily"][0]["submissions"] == 1
    assert everything["daily"][-1]["submissions"] == 1


def test_each_class_only_shows_its_own_students(teacher, classroom, make_class, make_teacher, make_student):
    submit(make_student("민수"))

    # 같은 교사의 다른 교실과, 다른 교사의 교실.
    second = make_class("2학년 2반")
    submit(make_student("현우", cls=second), "y = 2\n")
    colleague = make_teacher("teacher2")
    theirs = make_class("3학년 2반", by=colleague)
    outsider = make_student("지수", cls=theirs, by=colleague)
    submit(outsider, OTHER_CODE)
    submit(outsider, "x = 1\n")

    mine = dashboard(teacher, classroom)
    assert mine["class"]["name"] == "2학년 1반"
    assert [s["student_no"] for s in mine["students"]] == ["민수"]
    assert mine["summary"]["submissions"] == 1
    assert [s["student_no"] for s in dashboard(teacher, second)["students"]] == ["현우"]

    other = dashboard(colleague, theirs)
    assert [s["student_no"] for s in other["students"]] == ["지수"]
    assert other["summary"]["submissions"] == 2


def test_dashboard_never_exposes_code_or_hints(teacher, classroom, make_student):
    minsu = make_student("민수")
    submit(minsu, "secret_variable = 12345\nif secret_variable > 1\n    print(secret_variable)\n")

    text = teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard").text
    assert "secret_variable" not in text
    assert "12345" not in text
    assert "떠올려 보세요" not in text and "문장의 맨 끝" not in text  # 힌트 본문
