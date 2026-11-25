"""자기 설명 입력 (US-09): '다 고쳤어요' 때 원인을 한 줄 적고, 주인만 다시 본다."""

import json

import pytest

from app.collab import MAX_EXPLANATION_LENGTH

CODE = "for i in range(3):\nprint(i)\n"
NOTE = "for 아래 줄을 들여 쓰지 않았다"


def start(client, code=CODE):
    r = client.post("/api/submissions", json={"code": code})
    assert r.status_code == 201, r.text
    return r.json()["thread_id"]


def finish(client, tid, explanation=..., **kw):
    if explanation is ...:
        return client.post(f"/api/threads/{tid}/finish", **kw)
    return client.post(f"/api/threads/{tid}/finish", json={"explanation": explanation})


def test_explanation_is_saved_when_finishing(student):
    """AC-02: 한 줄을 적고 저장하면 코드가 완료되고 적은 내용이 남는다."""
    tid = start(student)
    r = finish(student, tid, NOTE)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fixed"
    assert r.json()["explanation"] == NOTE
    assert student.get(f"/api/threads/{tid}").json()["explanation"] == NOTE


@pytest.mark.parametrize("body", [None, {}, {"explanation": None}, {"explanation": ""}, {"explanation": "  \n "}])
def test_skipping_finishes_without_explanation(student, body):
    """AC-03: 아무것도 적지 않고 건너뛰어도 코드는 완료되고 설명은 빈 채로 남는다."""
    tid = start(student)
    r = student.post(f"/api/threads/{tid}/finish", json=body) if body is not None else finish(student, tid)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fixed"
    assert r.json()["explanation"] is None


def test_explanation_is_kept_on_one_line(student):
    tid = start(student)
    r = finish(student, tid, "  들여쓰기를\n빼먹었다\t ")
    assert r.json()["explanation"] == "들여쓰기를 빼먹었다"


def test_too_long_explanation_is_rejected_and_code_stays_open(student):
    tid = start(student)
    r = finish(student, tid, "가" * (MAX_EXPLANATION_LENGTH + 1))
    assert r.status_code == 400
    assert student.get(f"/api/threads/{tid}").json()["status"] == "open"
    assert finish(student, tid, "가" * MAX_EXPLANATION_LENGTH).status_code == 200


def test_explanation_can_be_rewritten_after_finishing(student):
    tid = start(student)
    finish(student, tid, NOTE)
    assert finish(student, tid, "다시 적은 원인").json()["explanation"] == "다시 적은 원인"
    assert finish(student, tid).json()["explanation"] == "다시 적은 원인"  # 설명 없이 다시 누르면 그대로


def test_dropped_code_cannot_get_an_explanation(student):
    old = start(student)
    start(student, "print('새 코드')\n")  # 새 코드를 시작하면 고치던 코드는 그만둔 것이 된다
    assert finish(student, old, NOTE).status_code == 409


def test_my_code_list_shows_my_explanation(student):
    """AC-04: 지난 코드 목록에서 내가 적은 설명이 보인다."""
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
    helper_tid = start(helper, "a = 1\n")
    finish(helper, helper_tid)
    assert helper.post("/api/help/start").status_code == 200
    assert helper.get(f"/api/threads/{tid}").json()["explanation"] is None  # 짝인 동안에도 설명은 없다

    finish(student, tid, NOTE)
    assert helper.get(f"/api/threads/{tid}").status_code == 404
    assert all(item["id"] != tid for item in helper.get("/api/threads").json())
    assert NOTE not in json.dumps(helper.get("/api/status").json(), ensure_ascii=False)


def test_teacher_dashboard_does_not_include_the_explanation(student, teacher, classroom):
    tid = start(student)
    finish(student, tid, NOTE)
    r = teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard")
    assert r.status_code == 200
    assert NOTE not in json.dumps(r.json(), ensure_ascii=False)
