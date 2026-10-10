from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import app

PASSWORD = "correct-horse-1"


@pytest.fixture
def make_browser():
    with ExitStack() as stack:
        yield lambda: stack.enter_context(TestClient(app))


@pytest.fixture
def make_account(client, make_browser):
    """계정을 만들고 로그인된 새 브라우저를 돌려준다."""

    def make(login_id="kim", name="김선생"):
        c = make_browser()
        r = c.post("/api/teacher/account/register", json={"login_id": login_id, "name": name, "password": PASSWORD})
        assert r.status_code == 201, r.text
        return c

    return make


@pytest.fixture
def acct(make_account):
    return make_account()


@pytest.fixture
def classroom(acct):
    r = acct.post("/api/teacher/classes", json={"name": "1반"})
    assert r.status_code == 201, r.text
    return r.json()


def add_student(make_browser, join_code, nickname):
    c = make_browser()
    assert c.post("/api/join", json={"join_code": join_code, "nickname": nickname}).status_code == 200
    return c


def submit(client, code="x = 1\n", thread_id=None):
    body = {"code": code}
    if thread_id is not None:
        body["thread_id"] = thread_id
    r = client.post("/api/submissions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def fix(client, thread_id):
    assert client.post(f"/api/threads/{thread_id}/finish").status_code == 200


def set_cases(findings):
    """submission_id -> case_id 로 findings 를 다시 쓴다 (임시 분석기는 항상 'stub' 만 낸다)."""
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM findings")
        for sub_id, case_id in findings:
            if case_id:
                conn.execute(
                    "INSERT INTO findings (submission_id, case_id, line, steps_json) VALUES (?, ?, NULL, '[]')",
                    (sub_id, case_id),
                )
        conn.execute("INSERT OR IGNORE INTO error_cases (case_id, category, title) VALUES ('loop', '반복문', '반복 범위 오류')")
        conn.execute("INSERT OR IGNORE INTO error_cases (case_id, category, title) VALUES ('var', '변수', '변수 초기화 누락')")
    conn.close()


# ---------------------------------------------------------------- 계정

def test_register_login_logout(client, make_browser):
    c = make_browser()
    assert c.get("/api/teacher/account/me").status_code == 401
    r = c.post("/api/teacher/account/register", json={"login_id": "Kim", "name": "김선생", "password": PASSWORD})
    assert r.status_code == 201
    assert c.get("/api/teacher/account/me").json() == {"login_id": "Kim", "name": "김선생"}

    assert c.post("/api/teacher/account/logout").status_code == 204
    assert c.get("/api/teacher/account/me").status_code == 401

    # 아이디는 대소문자를 구별하지 않는다
    r = c.post("/api/teacher/account/login", json={"login_id": "kim", "password": PASSWORD})
    assert r.status_code == 200
    assert c.get("/api/teacher/account/me").status_code == 200


def test_register_rejects_duplicates_and_weak_input(client, make_browser, make_account):
    make_account("kim")
    c = make_browser()
    dup = c.post("/api/teacher/account/register", json={"login_id": "KIM", "name": "다른 사람", "password": PASSWORD})
    assert dup.status_code == 409
    assert c.post("/api/teacher/account/register", json={"login_id": "ab", "name": "x", "password": PASSWORD}).status_code == 422
    assert c.post("/api/teacher/account/register", json={"login_id": "lee", "name": "x", "password": "short"}).status_code == 422


def test_wrong_login_gives_same_error_for_unknown_id_and_bad_password(client, make_browser, make_account):
    make_account("kim")
    c = make_browser()
    bad_pw = c.post("/api/teacher/account/login", json={"login_id": "kim", "password": "wrong-password"})
    no_user = c.post("/api/teacher/account/login", json={"login_id": "nobody", "password": "wrong-password"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()


def test_password_is_stored_hashed(client, make_account):
    make_account("kim")
    conn = get_connection()
    stored = conn.execute("SELECT password_hash FROM teachers WHERE login_id = 'kim'").fetchone()[0]
    conn.close()
    assert PASSWORD not in stored and stored.startswith("scrypt$")


def test_student_and_legacy_teacher_cookies_cannot_open_portal(client, make_browser, classroom):
    student = add_student(make_browser, classroom["join_code"], "철수")
    assert student.get("/api/teacher/classes").status_code == 401

    legacy = make_browser()
    old = client.post("/api/classes", json={"name": "옛 학급"}).json()
    assert legacy.post("/api/teacher/login", json={"join_code": old["join_code"], "teacher_key": old["teacher_key"]}).status_code == 200
    assert legacy.get("/api/teacher/classes").status_code == 401


# ---------------------------------------------------------------- 학급

def test_class_list_only_shows_my_classes(acct, make_account, classroom):
    other = make_account("lee", "이선생")
    other.post("/api/teacher/classes", json={"name": "남의 반"})
    names = [c["name"] for c in acct.get("/api/teacher/classes").json()["classes"]]
    assert names == ["1반"]


def test_claim_existing_class_with_teacher_key(client, acct, make_account):
    old = client.post("/api/classes", json={"name": "옛 학급"}).json()
    assert acct.post("/api/teacher/classes/claim", json={"join_code": old["join_code"], "teacher_key": "wrong"}).status_code == 401
    r = acct.post("/api/teacher/classes/claim", json={"join_code": old["join_code"], "teacher_key": old["teacher_key"]})
    assert r.status_code == 200
    assert "옛 학급" in [c["name"] for c in acct.get("/api/teacher/classes").json()["classes"]]

    other = make_account("lee", "이선생")
    again = other.post("/api/teacher/classes/claim", json={"join_code": old["join_code"], "teacher_key": old["teacher_key"]})
    assert again.status_code == 409


def test_other_teachers_class_is_404_everywhere(acct, make_account, classroom):
    other = make_account("lee", "이선생")
    cid = classroom["id"]
    for method, path, body in [
        ("get", f"/api/teacher/classes/{cid}/dashboard", None),
        ("get", f"/api/teacher/classes/{cid}/students/1", None),
        ("post", f"/api/teacher/classes/{cid}/units", {"title": "변수"}),
        ("put", f"/api/teacher/classes/{cid}/current-unit", {"unit_id": None}),
    ]:
        r = getattr(other, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 404, (method, path)


# ---------------------------------------------------------------- 학생별 통계

@pytest.fixture
def populated(acct, classroom, make_browser):
    """철수: 코드 2개(하나 고침) 제출 4번 중 3번 오답 / 영희: 코드 1개 고침 / 민수: 입장만."""
    code = classroom["join_code"]
    chul = add_student(make_browser, code, "철수")
    young = add_student(make_browser, code, "영희")
    add_student(make_browser, code, "민수")

    a = submit(chul, "a = 1\n")
    b = submit(chul, "a = 2\n", thread_id=a["thread_id"])
    fix(chul, a["thread_id"])
    c = submit(chul, "b = 1\n")
    d = submit(chul, "b = 2\n", thread_id=c["thread_id"])
    e = submit(young, "c = 1\n")
    fix(young, e["thread_id"])

    ids = lambda r: r["submission"]["id"] if "submission" in r else r["id"]  # noqa: E731
    sub = [ids(x) for x in (a, b, c, d, e)]
    # a: var / b: 정상 / c: loop / d: loop / e: loop
    set_cases([(sub[0], "var"), (sub[1], None), (sub[2], "loop"), (sub[3], "loop"), (sub[4], "loop")])
    return classroom


def test_dashboard_students_error_rate_and_ranking(acct, populated):
    data = acct.get(f"/api/teacher/classes/{populated['id']}/dashboard").json()
    students = {s["nickname"]: s for s in data["students"]}

    assert students["철수"]["submissions"] == 4
    assert students["철수"]["error_submissions"] == 3
    assert students["철수"]["error_rate_pct"] == 75.0
    assert students["철수"]["top_case_title"] == "반복 범위 오류"
    assert students["영희"]["error_rate_pct"] == 100.0
    assert students["민수"]["error_rate_pct"] is None  # 제출이 없으면 오답률도 없다

    # 오답률 높은 학생이 위, 제출 없는 학생은 맨 아래
    assert [s["nickname"] for s in data["students"]] == ["영희", "철수", "민수"]

    ranking = data["error_ranking"]
    assert [(r["rank"], r["case_id"], r["students"], r["threads"]) for r in ranking] == [
        (1, "loop", 2, 2),
        (2, "var", 1, 1),
    ]
    assert ranking[0]["title"] == "반복 범위 오류"

    summary = data["class"]
    assert (summary["students_total"], summary["students_active"], summary["submissions"]) == (3, 2, 5)
    assert summary["error_rate_pct"] == 80.0


def test_student_detail_ranking_and_unit_progress(acct, populated):
    cid = populated["id"]
    acct.post(f"/api/teacher/classes/{cid}/units", json={"title": "반복문", "target_fixed": 2})
    students = acct.get(f"/api/teacher/classes/{cid}/dashboard").json()["students"]
    sid = next(s["id"] for s in students if s["nickname"] == "철수")

    detail = acct.get(f"/api/teacher/classes/{cid}/students/{sid}").json()
    assert detail["student"]["nickname"] == "철수"
    # 코드 수는 둘 다 1개지만 loop 가 의심 지점이 2번이라 1위다
    assert [(r["rank"], r["case_id"]) for r in detail["error_ranking"]] == [(1, "loop"), (2, "var")]
    assert round(sum(r["share_pct"] for r in detail["error_ranking"])) == 100
    # 단원을 만들기 전에 시작한 코드는 단원이 없어서 진도에 잡히지 않는다
    assert detail["progress"][0]["title"] == "반복문"
    assert detail["progress"][0]["fixed_threads"] == 0

    assert acct.get(f"/api/teacher/classes/{cid}/students/99999").status_code == 404


def test_dashboard_contains_no_code_hints_or_chat(acct, populated):
    text = acct.get(f"/api/teacher/classes/{populated['id']}/dashboard").text
    for secret in ("a = 1", "b = 2", "steps", "hint", "body"):
        assert secret not in text


# ---------------------------------------------------------------- 진도 관리

def test_first_unit_becomes_current_and_new_threads_join_it(acct, classroom, make_browser):
    cid = classroom["id"]
    r = acct.post(f"/api/teacher/classes/{cid}/units", json={"title": "변수", "target_fixed": 2})
    assert r.status_code == 201 and r.json()["is_current"] is True
    second = acct.post(f"/api/teacher/classes/{cid}/units", json={"title": "반복문"}).json()
    assert second["is_current"] is False
    assert acct.post(f"/api/teacher/classes/{cid}/units", json={"title": "변수"}).status_code == 409

    chul = add_student(make_browser, classroom["join_code"], "철수")
    t1 = submit(chul)["thread_id"]
    fix(chul, t1)

    data = acct.get(f"/api/teacher/classes/{cid}/dashboard").json()
    units = {u["title"]: u for u in data["units"]}
    assert units["변수"]["students_started"] == 1
    assert units["변수"]["avg_progress_pct"] == 50.0  # 목표 2개 중 1개 고침
    assert units["변수"]["students_done"] == 0
    assert data["class"]["current_unit_title"] == "변수"

    t2 = submit(chul)["thread_id"]
    fix(chul, t2)
    data = acct.get(f"/api/teacher/classes/{cid}/dashboard").json()
    assert {u["title"]: u for u in data["units"]}["변수"]["students_done"] == 1
    assert data["class"]["current_unit_completion_pct"] == 100.0
    assert data["students"][0]["current_unit_progress_pct"] == 100.0


def test_change_current_unit_and_status(acct, classroom, make_browser):
    cid = classroom["id"]
    first = acct.post(f"/api/teacher/classes/{cid}/units", json={"title": "변수"}).json()["id"]
    second = acct.post(f"/api/teacher/classes/{cid}/units", json={"title": "반복문"}).json()["id"]

    assert acct.put(f"/api/teacher/classes/{cid}/current-unit", json={"unit_id": second}).status_code == 200
    units = {u["id"]: u for u in acct.get(f"/api/teacher/classes/{cid}/dashboard").json()["units"]}
    assert units[second]["status"] == "active"  # 예정이던 단원이 진행 중으로 바뀐다

    assert acct.patch(f"/api/teacher/classes/{cid}/units/{first}", json={"status": "done", "target_fixed": 3}).status_code == 200
    units = {u["id"]: u for u in acct.get(f"/api/teacher/classes/{cid}/dashboard").json()["units"]}
    assert (units[first]["status"], units[first]["target_fixed"]) == ("done", 3)

    assert acct.patch(f"/api/teacher/classes/{cid}/units/{first}", json={"status": "bogus"}).status_code == 422
    assert acct.patch(f"/api/teacher/classes/{cid}/units/{first}", json={}).status_code == 422
    assert acct.put(f"/api/teacher/classes/{cid}/current-unit", json={"unit_id": 99999}).status_code == 404

    # 새로 시작하는 코드는 바뀐 현재 단원에 묶인다
    chul = add_student(make_browser, classroom["join_code"], "철수")
    submit(chul)
    rows = acct.get(f"/api/teacher/classes/{cid}/units/{second}/students").json()["students"]
    assert [(r["nickname"], r["started_threads"]) for r in rows] == [("철수", 1)]


def test_cannot_use_another_classes_unit(acct, classroom):
    other = acct.post("/api/teacher/classes", json={"name": "2반"}).json()
    foreign = acct.post(f"/api/teacher/classes/{other['id']}/units", json={"title": "변수"}).json()["id"]
    cid = classroom["id"]
    assert acct.put(f"/api/teacher/classes/{cid}/current-unit", json={"unit_id": foreign}).status_code == 404
    assert acct.patch(f"/api/teacher/classes/{cid}/units/{foreign}", json={"status": "done"}).status_code == 404
    assert acct.get(f"/api/teacher/classes/{cid}/units/{foreign}/students").status_code == 404


def test_empty_class_dashboard(acct, classroom):
    data = acct.get(f"/api/teacher/classes/{classroom['id']}/dashboard").json()
    assert data["students"] == [] and data["error_ranking"] == [] and data["units"] == []
    assert data["class"]["students_total"] == 0 and data["class"]["error_rate_pct"] is None
