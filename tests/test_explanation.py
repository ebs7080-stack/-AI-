"""자기 설명 입력 (US-09): '다 고쳤어요' 때 어려웠던 점 등을 자유롭게 적는다.

글은 주인이 다시 보고, 그 교실의 교사가 대시보드에서 이름과 함께 본다 (2026-11-25 사용자 결정).
짝이 된 도우미를 비롯한 다른 학생에게는 보이지 않는다.
"""

import json

import pytest

from app.collab import MAX_EXPLANATION_LENGTH
from app.db import get_connection

CODE = "for i in range(3):\nprint(i)\n"
NOTE = "for 아래 줄을 들여 써야 한다는 게 헷갈렸어요"


def start(client, code=CODE):
    r = client.post("/api/submissions", json={"code": code})
    assert r.status_code == 201, r.text
    return r.json()["thread_id"]


def finish(client, tid, explanation=...):
    if explanation is ...:
        return client.post(f"/api/threads/{tid}/finish")
    return client.post(f"/api/threads/{tid}/finish", json={"explanation": explanation})


def dashboard(teacher, cls, **params):
    r = teacher.get(f"/api/teacher/classes/{cls['id']}/dashboard", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 학생 쪽 ----------


def test_explanation_is_saved_when_finishing(student):
    """AC-02: 생각을 적고 저장하면 코드가 완료되고 적은 내용이 남는다."""
    tid = start(student)
    r = finish(student, tid, NOTE)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fixed"
    assert r.json()["explanation"] == NOTE
    assert student.get(f"/api/threads/{tid}").json()["explanation"] == NOTE


@pytest.mark.parametrize("body", [None, {}, {"explanation": None}, {"explanation": ""}, {"explanation": "  \n "}])
def test_skipping_finishes_without_explanation(student, body):
    """AC-03: 아무것도 적지 않고 건너뛰어도 코드는 완료되고 글은 빈 채로 남는다."""
    tid = start(student)
    r = finish(student, tid) if body is None else student.post(f"/api/threads/{tid}/finish", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fixed"
    assert r.json()["explanation"] is None


def test_free_text_keeps_line_breaks_but_trims_blank_space(student):
    tid = start(student)
    r = finish(student, tid, "\n  들여쓰기가 어려웠다  \r\n\n\n\n그래도 알게 됐다\t\n\n")
    assert r.json()["explanation"] == "  들여쓰기가 어려웠다\n\n그래도 알게 됐다"


def test_too_long_explanation_is_rejected_and_code_stays_open(student):
    tid = start(student)
    r = finish(student, tid, "가" * (MAX_EXPLANATION_LENGTH + 1))
    assert r.status_code == 400
    assert student.get(f"/api/threads/{tid}").json()["status"] == "open"
    assert finish(student, tid, "가" * MAX_EXPLANATION_LENGTH).status_code == 200


def test_explanation_can_be_rewritten_after_finishing(student):
    tid = start(student)
    finish(student, tid, NOTE)
    assert finish(student, tid, "다시 적은 생각").json()["explanation"] == "다시 적은 생각"
    assert finish(student, tid).json()["explanation"] == "다시 적은 생각"  # 글 없이 다시 누르면 그대로


def test_dropped_code_cannot_get_an_explanation(student):
    old = start(student)
    start(student, "print('새 코드')\n")  # 새 코드를 시작하면 고치던 코드는 그만둔 것이 된다
    assert finish(student, old, NOTE).status_code == 409


def test_my_code_list_shows_my_explanation(student):
    """AC-04: 지난 코드 목록에서 내가 남긴 글이 보인다."""
    first = start(student)
    finish(student, first, NOTE)
    second = start(student, "print('두 번째')\n")
    finish(student, second)
    items = {item["id"]: item for item in student.get("/api/threads").json()}
    assert items[first]["explanation"] == NOTE
    assert items[second]["explanation"] is None


def test_other_students_never_see_the_explanation(student, make_student, open_help_time):
    """AC-04: 다른 학생에게는 보이지 않는다 (남의 코드는 404, 짝이던 도우미도 짝이 끝나면 404)."""
    open_help_time()
    tid = start(student)
    helper = make_student("영희")
    finish(helper, start(helper, "a = 1\n"))
    assert helper.post("/api/help/start").status_code == 200
    assert helper.get(f"/api/threads/{tid}").json()["explanation"] is None  # 짝인 동안에도 글은 없다

    finish(student, tid, NOTE)
    assert helper.get(f"/api/threads/{tid}").status_code == 404
    assert all(item["id"] != tid for item in helper.get("/api/threads").json())
    assert NOTE not in json.dumps(helper.get("/api/status").json(), ensure_ascii=False)


# ---------- 교사 대시보드 ----------


def test_teacher_sees_the_writing_with_the_students_name(student, teacher, classroom):
    finish(student, start(student), NOTE)
    items = dashboard(teacher, classroom)["reflections"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["text"] == NOTE
    assert (item["student_no"], item["name"]) == ("철수", "철수")
    assert item["errors"]  # 그 코드에서 만난 오류 이름
    assert "print(i)" not in json.dumps(items, ensure_ascii=False)  # 코드 원문은 여전히 내보내지 않는다


def test_dashboard_lists_newest_first_and_skips_empty_or_unfinished(student, teacher, classroom):
    finish(student, start(student), "첫 번째 생각")
    finish(student, start(student, "print('건너뜀')\n"))
    finish(student, start(student, "print('마지막')\n"), "두 번째 생각")
    start(student, "print('고치는 중')\n")  # 아직 끝내지 않은 코드
    reflections = dashboard(teacher, classroom)["reflections"]
    assert reflections["total"] == 2
    assert [i["text"] for i in reflections["items"]] == ["두 번째 생각", "첫 번째 생각"]


def test_reflections_come_in_pages_for_more_button(student, teacher, classroom):
    """처음에는 REFLECTIONS_PAGE 개만, '더 보기'가 reflections= 로 개수를 늘린다. 50개 제한은 없다."""
    from app.stats import REFLECTIONS_PAGE

    count = REFLECTIONS_PAGE + 5
    for n in range(count):
        finish(student, start(student, f"print({n})\n"), f"생각 {n}")

    first = dashboard(teacher, classroom)["reflections"]
    assert (first["total"], len(first["items"]), first["has_more"]) == (count, REFLECTIONS_PAGE, True)
    assert first["items"][0]["text"] == f"생각 {count - 1}"  # 최근 것부터

    more = dashboard(teacher, classroom, reflections=REFLECTIONS_PAGE * 2)["reflections"]
    assert (len(more["items"]), more["has_more"]) == (count, False)
    assert [i["text"] for i in more["items"][:REFLECTIONS_PAGE]] == [i["text"] for i in first["items"]]


@pytest.mark.parametrize("value", [0, -1, 1001, "많이"])
def test_reflections_count_must_be_in_range(teacher, classroom, value):
    r = teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard", params={"reflections": value})
    assert r.status_code == 422


def test_another_class_is_not_mixed_in(student, teacher, classroom, make_class, make_student):
    other_class = make_class("다른 반")
    other = make_student("영희", cls=other_class)
    finish(other, start(other), "다른 반 학생의 글")
    finish(student, start(student), NOTE)
    assert [i["text"] for i in dashboard(teacher, classroom)["reflections"]["items"]] == [NOTE]
    assert [i["text"] for i in dashboard(teacher, other_class)["reflections"]["items"]] == ["다른 반 학생의 글"]


def test_other_teacher_cannot_read_the_writing(student, classroom, make_teacher):
    finish(student, start(student), NOTE)
    stranger = make_teacher("stranger")
    assert stranger.get(f"/api/teacher/classes/{classroom['id']}/dashboard").status_code == 404


def test_reflections_follow_the_period_filter(student, teacher, classroom):
    tid = start(student)
    finish(student, tid, NOTE)
    conn = get_connection()
    conn.execute("UPDATE threads SET closed_at = datetime('now', '-30 days') WHERE id = ?", (tid,))
    conn.commit()
    conn.close()
    assert dashboard(teacher, classroom, days=7)["reflections"]["total"] == 0
    assert dashboard(teacher, classroom)["reflections"]["total"] == 1
