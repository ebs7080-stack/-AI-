"""수업 단원과 진도 관리 (FR-3).

교사가 교실마다 단원을 만들고 '지금 수업하는 단원'을 정하면, 그 뒤에 학생이 새로 시작하는
코드가 그 단원의 진도로 쌓인다. 진도는 단원의 목표 개수(target_fixed)를 기준으로 센다.
"""

import pytest

from app.db import get_connection

CODE = "for i in range(3):\nprint(i)\n"
OTHER_CODE = "total = 0\nfor n in range(5):\n    total += n\n"


@pytest.fixture
def classroom(make_class):
    return make_class("2학년 1반")


def units(client, cls):
    r = client.get(f"/api/teacher/classes/{cls['id']}/units")
    assert r.status_code == 200, r.text
    return r.json()


def add_unit(client, cls, title, target_fixed=1):
    r = client.post(
        f"/api/teacher/classes/{cls['id']}/units",
        json={"title": title, "target_fixed": target_fixed},
    )
    assert r.status_code == 201, r.text
    return r.json()


def set_current(client, cls, unit_id):
    r = client.put(f"/api/teacher/classes/{cls['id']}/current-unit", json={"unit_id": unit_id})
    assert r.status_code == 200, r.text
    return r.json()


def by_title(payload, title):
    return next(u for u in payload["units"] if u["title"] == title)


def fix_one(student, code=CODE):
    """코드를 한 번 제출하고 다 고친 것으로 끝낸다 (스레드 하나가 fixed 가 된다)."""
    thread_id = student.post("/api/submissions", json={"code": code}).json()["thread_id"]
    assert student.post(f"/api/threads/{thread_id}/finish").status_code == 200
    return thread_id


def dashboard(client, cls):
    r = client.get(f"/api/teacher/classes/{cls['id']}/dashboard")
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 단원 만들기와 고치기 ----------


def test_new_class_has_no_units(teacher, classroom):
    assert units(teacher, classroom) == {"current_unit_id": None, "units": []}


def test_first_unit_becomes_the_current_one(teacher, classroom):
    created = add_unit(teacher, classroom, "변수")
    unit = created["units"][0]
    assert created["current_unit_id"] == unit["id"]
    assert unit["is_current"] is True
    assert unit["status"] == "active"
    # 두 번째 단원은 뒤에 붙기만 하고 지금 단원을 빼앗지 않는다.
    second = add_unit(teacher, classroom, "반복문")
    assert second["current_unit_id"] == unit["id"]
    assert [u["title"] for u in second["units"]] == ["변수", "반복문"]
    assert by_title(second, "반복문")["status"] == "planned"


def test_duplicate_title_in_same_class_is_rejected(teacher, classroom, make_class):
    add_unit(teacher, classroom, "변수")
    r = teacher.post(f"/api/teacher/classes/{classroom['id']}/units", json={"title": "변수"})
    assert r.status_code == 409
    # 다른 교실에서는 같은 이름을 다시 쓸 수 있다.
    other = make_class("2학년 2반")
    add_unit(teacher, other, "변수")


def test_blank_title_and_bad_target_are_rejected(teacher, classroom):
    for body in ({"title": "   "}, {"title": "변수", "target_fixed": 0}, {"title": "변수", "target_fixed": 999}):
        assert teacher.post(f"/api/teacher/classes/{classroom['id']}/units", json=body).status_code == 422


def test_teacher_can_edit_title_target_and_order(teacher, classroom):
    unit = add_unit(teacher, classroom, "변수")["units"][0]
    r = teacher.patch(
        f"/api/teacher/classes/{classroom['id']}/units/{unit['id']}",
        json={"title": "변수와 자료형", "target_fixed": 3, "status": "done", "position": 5},
    )
    assert r.status_code == 200, r.text
    edited = r.json()["units"][0]
    assert (edited["title"], edited["target_fixed"], edited["status"], edited["position"]) == (
        "변수와 자료형",
        3,
        "done",
        5,
    )


def test_unknown_status_and_empty_patch_are_rejected(teacher, classroom):
    unit = add_unit(teacher, classroom, "변수")["units"][0]
    url = f"/api/teacher/classes/{classroom['id']}/units/{unit['id']}"
    assert teacher.patch(url, json={"status": "끝"}).status_code == 422
    assert teacher.patch(url, json={}).status_code == 422


def test_unused_unit_can_be_deleted(teacher, classroom):
    first = add_unit(teacher, classroom, "변수")["units"][0]
    add_unit(teacher, classroom, "반복문")
    r = teacher.delete(f"/api/teacher/classes/{classroom['id']}/units/{first['id']}")
    assert r.status_code == 200, r.text
    # 지금 단원을 지웠으니 지금 단원이 없어진다 (남은 단원이 자동으로 되지는 않는다).
    assert r.json()["current_unit_id"] is None
    assert [u["title"] for u in r.json()["units"]] == ["반복문"]


def test_unit_with_submissions_cannot_be_deleted(teacher, classroom, make_student):
    unit = add_unit(teacher, classroom, "변수")["units"][0]
    student = make_student("10101", cls=classroom)
    student.post("/api/submissions", json={"code": CODE})
    r = teacher.delete(f"/api/teacher/classes/{classroom['id']}/units/{unit['id']}")
    assert r.status_code == 409


# ---------- 다른 교사의 단원은 보이지 않는다 ----------


def test_other_teachers_units_are_hidden(teacher, make_teacher, make_class, classroom):
    unit = add_unit(teacher, classroom, "변수")["units"][0]
    other = make_teacher("teacher2")
    base = f"/api/teacher/classes/{classroom['id']}"
    assert other.get(f"{base}/units").status_code == 404
    assert other.post(f"{base}/units", json={"title": "몰래"}).status_code == 404
    assert other.patch(f"{base}/units/{unit['id']}", json={"title": "몰래"}).status_code == 404
    assert other.delete(f"{base}/units/{unit['id']}").status_code == 404
    assert other.put(f"{base}/current-unit", json={"unit_id": unit["id"]}).status_code == 404


def test_current_unit_must_be_in_the_same_class(teacher, classroom, make_class):
    other = make_class("2학년 2반")
    foreign = add_unit(teacher, other, "변수")["units"][0]
    r = teacher.put(
        f"/api/teacher/classes/{classroom['id']}/current-unit", json={"unit_id": foreign["id"]}
    )
    assert r.status_code == 404
    assert units(teacher, classroom)["current_unit_id"] is None


def test_database_itself_refuses_a_foreign_current_unit(teacher, classroom, make_class):
    """API 를 거치지 않고 넣어도 트리거가 막는다 (진도가 엉키지 않게 DB 가 보장한다)."""
    import sqlite3

    other = make_class("2학년 2반")
    foreign = add_unit(teacher, other, "변수")["units"][0]
    conn = get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    "UPDATE classes SET current_unit_id = ? WHERE id = ?",
                    (foreign["id"], classroom["id"]),
                )
    finally:
        conn.close()


# ---------- 학생의 코드가 단원에 담긴다 ----------


def test_new_thread_joins_the_current_unit(teacher, classroom, make_student):
    unit = add_unit(teacher, classroom, "변수")["units"][0]
    student = make_student("10101", cls=classroom)
    thread_id = student.post("/api/submissions", json={"code": CODE}).json()["thread_id"]

    conn = get_connection()
    row = conn.execute("SELECT unit_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
    conn.close()
    assert row["unit_id"] == unit["id"]


def test_thread_keeps_its_unit_when_the_teacher_moves_on(teacher, classroom, make_student):
    """단원을 넘겨도 이미 시작한 코드는 원래 단원에 남는다 (지난 진도가 되돌아가지 않는다)."""
    first = add_unit(teacher, classroom, "변수")["units"][0]
    second = by_title(add_unit(teacher, classroom, "반복문"), "반복문")
    student = make_student("10101", cls=classroom)
    old_thread = student.post("/api/submissions", json={"code": CODE}).json()["thread_id"]

    set_current(teacher, classroom, second["id"])
    new_thread = student.post("/api/submissions", json={"code": OTHER_CODE}).json()["thread_id"]

    conn = get_connection()
    rows = dict(conn.execute("SELECT id, unit_id FROM threads").fetchall())
    conn.close()
    assert rows[old_thread] == first["id"]
    assert rows[new_thread] == second["id"]


def test_without_a_current_unit_threads_belong_to_none(teacher, classroom, make_student):
    student = make_student("10101", cls=classroom)
    thread_id = student.post("/api/submissions", json={"code": CODE}).json()["thread_id"]
    conn = get_connection()
    row = conn.execute("SELECT unit_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
    conn.close()
    assert row["unit_id"] is None


def test_moving_on_marks_the_passed_unit_done(teacher, classroom):
    first = add_unit(teacher, classroom, "변수")["units"][0]
    second = by_title(add_unit(teacher, classroom, "반복문"), "반복문")
    moved = set_current(teacher, classroom, second["id"])
    assert by_title(moved, "변수")["status"] == "done"
    assert by_title(moved, "반복문")["status"] == "active"
    assert moved["current_unit_id"] == second["id"]
    # 다시 앞 단원으로 돌아가도 받아 준다 (복습 수업).
    back = set_current(teacher, classroom, first["id"])
    assert by_title(back, "변수")["is_current"] is True


def test_current_unit_can_be_cleared(teacher, classroom):
    add_unit(teacher, classroom, "변수")
    r = teacher.put(f"/api/teacher/classes/{classroom['id']}/current-unit", json={"unit_id": None})
    assert r.status_code == 200
    assert r.json()["current_unit_id"] is None
    assert units(teacher, classroom)["units"][0]["is_current"] is False


# ---------- 진도 ----------


def test_progress_counts_only_finished_code(teacher, classroom, make_student):
    add_unit(teacher, classroom, "변수", target_fixed=2)
    student = make_student("10101", cls=classroom)

    # 제출만 하고 끝내지 않은 코드는 진도에 안 들어간다.
    student.post("/api/submissions", json={"code": CODE})
    progress = units(teacher, classroom)["units"][0]
    assert (progress["fixed_threads"], progress["students_done"]) == (0, 0)
    assert progress["threads"] == 1

    fix_one(student, OTHER_CODE)  # 1/2 → 50%
    progress = units(teacher, classroom)["units"][0]
    assert progress["fixed_threads"] == 1
    assert progress["avg_progress_pct"] == 50.0
    assert progress["students_done"] == 0

    fix_one(student, "x = 1\nprint(x)\n")  # 2/2 → 100%
    progress = units(teacher, classroom)["units"][0]
    assert (progress["students_done"], progress["students_total"]) == (1, 1)
    assert (progress["done_pct"], progress["avg_progress_pct"]) == (100.0, 100.0)


def test_progress_never_passes_100_percent(teacher, classroom, make_student):
    add_unit(teacher, classroom, "변수", target_fixed=1)
    student = make_student("10101", cls=classroom)
    fix_one(student, CODE)
    fix_one(student, OTHER_CODE)  # 목표보다 하나 더
    progress = units(teacher, classroom)["units"][0]
    assert progress["fixed_threads"] == 2
    assert progress["avg_progress_pct"] == 100.0


def test_class_progress_divides_by_the_whole_roster(teacher, classroom, make_student):
    """한 번도 제출하지 않은 학생도 분모에 들어간다 (반 전체 진도여야 하므로)."""
    add_unit(teacher, classroom, "변수", target_fixed=1)
    done = make_student("10101", cls=classroom)
    make_student("10102", cls=classroom)  # 아무것도 하지 않은 학생
    fix_one(done)

    progress = units(teacher, classroom)["units"][0]
    assert (progress["students_done"], progress["students_total"]) == (1, 2)
    assert progress["done_pct"] == 50.0
    assert progress["avg_progress_pct"] == 50.0


def test_empty_class_has_no_progress_numbers(teacher, classroom):
    progress = add_unit(teacher, classroom, "변수")["units"][0]
    assert progress["students_total"] == 0
    assert progress["done_pct"] is None
    assert progress["avg_progress_pct"] is None


def test_raising_the_target_lowers_the_progress(teacher, classroom, make_student):
    unit = add_unit(teacher, classroom, "변수", target_fixed=1)["units"][0]
    student = make_student("10101", cls=classroom)
    fix_one(student)
    assert units(teacher, classroom)["units"][0]["done_pct"] == 100.0

    teacher.patch(
        f"/api/teacher/classes/{classroom['id']}/units/{unit['id']}", json={"target_fixed": 2}
    )
    progress = units(teacher, classroom)["units"][0]
    assert (progress["students_done"], progress["avg_progress_pct"]) == (0, 50.0)


def test_other_units_progress_is_not_mixed_in(teacher, classroom, make_student):
    first = add_unit(teacher, classroom, "변수")["units"][0]
    second = by_title(add_unit(teacher, classroom, "반복문"), "반복문")
    student = make_student("10101", cls=classroom)
    fix_one(student, CODE)  # 변수 단원에서 하나

    set_current(teacher, classroom, second["id"])
    after = units(teacher, classroom)
    assert by_title(after, "변수")["fixed_threads"] == 1
    assert by_title(after, "반복문")["fixed_threads"] == 0
    assert by_title(after, "반복문")["avg_progress_pct"] == 0.0
    assert first["id"] != second["id"]


# ---------- 대시보드에 함께 나온다 ----------


def test_dashboard_shows_units_and_per_student_progress(teacher, classroom, make_student):
    add_unit(teacher, classroom, "변수", target_fixed=2)
    student = make_student("10101", cls=classroom)
    make_student("10102", cls=classroom)
    fix_one(student)

    data = dashboard(teacher, classroom)
    assert [u["title"] for u in data["units"]] == ["변수"]
    assert data["units"][0]["is_current"] is True

    rows = {s["student_no"]: s for s in data["students"]}
    assert (rows["10101"]["unit_fixed"], rows["10101"]["unit_progress_pct"]) == (1, 50.0)
    assert (rows["10102"]["unit_fixed"], rows["10102"]["unit_progress_pct"]) == (0, 0.0)
    assert rows["10101"]["unit_target"] == 2


def test_dashboard_leaves_progress_blank_without_a_current_unit(teacher, classroom, make_student):
    student = make_student("10101", cls=classroom)
    fix_one(student)
    data = dashboard(teacher, classroom)
    assert data["units"] == []
    assert data["students"][0]["unit_progress_pct"] is None
    assert data["students"][0]["unit_target"] is None


def test_unit_progress_ignores_the_period_filter(teacher, classroom, make_student):
    """기간을 좁혀도 단원 진도는 그대로다 (학기 내내 쌓이는 값이라서)."""
    add_unit(teacher, classroom, "변수")
    student = make_student("10101", cls=classroom)
    fix_one(student)
    conn = get_connection()
    with conn:
        conn.execute("UPDATE threads SET created_at = datetime('now', '-30 days')")
        conn.execute("UPDATE submissions SET created_at = datetime('now', '-30 days')")
    conn.close()

    r = teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard", params={"days": 7})
    data = r.json()
    assert data["summary"]["submissions"] == 0  # 기간 밖이라 요약에서는 빠진다
    assert data["units"][0]["students_done"] == 1  # 진도는 남아 있다
