"""도움 시간: 친구 도와주러 가기와 채팅은 교사가 연 시간 동안만 된다."""

from app.db import get_connection

CODE = "for i in range(3):\nprint(i)\n"


def submit(client, code=CODE):
    r = client.post("/api/submissions", json={"code": code})
    assert r.status_code == 201, r.text
    return r.json()


def finish_own_code(client):
    tid = submit(client, "a = 1\n")["thread_id"]
    assert client.post(f"/api/threads/{tid}/finish").status_code == 200


def help_time_url(cls):
    return f"/api/teacher/classes/{cls['id']}/help-time"


def let_time_run_out():
    """시간이 흘러 도움 시간이 저절로 끝난 상황을 만든다."""
    conn = get_connection()
    with conn:
        conn.execute("UPDATE classes SET help_until = datetime('now', '-1 seconds')")
    conn.close()


def make_pair(student, make_student):
    """철수(고치는 중)와 그를 돕는 영희. 도움 시간이 열려 있어야 한다."""
    thread = submit(student)
    helper = make_student("영희")
    finish_own_code(helper)
    matched = helper.post("/api/help/start")
    assert matched.status_code == 200, matched.text
    return helper, thread, matched.json()


# ---------- 교사: 열기, 늘리기, 끝내기 ----------


def test_help_time_is_closed_by_default(teacher, classroom):
    closed = {"active": False, "ends_at": None, "remaining_seconds": 0}
    assert teacher.get(f"/api/teacher/classes/{classroom['id']}").json()["help_time"] == closed
    assert teacher.get("/api/teacher/classes").json()[0]["help_time"] == closed
    assert teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard").json()["help_time"] == closed


def test_open_extend_and_end(teacher, classroom):
    url = help_time_url(classroom)
    opened = teacher.post(url, json={"minutes": 5}).json()
    assert opened["active"] is True and opened["ends_at"].endswith("+00:00")
    assert 295 <= opened["remaining_seconds"] <= 300

    extended = teacher.post(url, json={"minutes": 1}).json()  # 1분 추가
    assert 355 <= extended["remaining_seconds"] <= 360

    ended = teacher.delete(url)  # 즉시 종료
    assert ended.status_code == 200
    assert ended.json() == {"active": False, "ends_at": None, "remaining_seconds": 0}
    assert teacher.delete(url).status_code == 200  # 닫혀 있을 때 다시 눌러도 괜찮다


def test_opening_again_after_time_ran_out_starts_from_now(teacher, classroom):
    url = help_time_url(classroom)
    teacher.post(url, json={"minutes": 5})
    let_time_run_out()
    assert teacher.get(f"/api/teacher/classes/{classroom['id']}").json()["help_time"]["active"] is False
    again = teacher.post(url, json={"minutes": 2}).json()
    assert 115 <= again["remaining_seconds"] <= 120


def test_minutes_must_be_in_range(teacher, classroom):
    url = help_time_url(classroom)
    for minutes in (0, -1, 181, 1.5):
        assert teacher.post(url, json={"minutes": minutes}).status_code == 422
    assert teacher.post(url, json={}).status_code == 422


def test_only_the_owner_teacher_controls_help_time(teacher, classroom, make_teacher, student, client):
    url = help_time_url(classroom)
    stranger = make_teacher("teacher2")
    assert stranger.post(url, json={"minutes": 5}).status_code == 404
    assert stranger.delete(url).status_code == 404
    assert student.post(url, json={"minutes": 5}).status_code == 401  # 학생은 열 수 없다
    assert student.get("/api/status").json()["help_time"]["active"] is False


def test_help_time_is_per_class(teacher, classroom, make_class, make_student, open_help_time):
    other = make_class("2학년 1반")
    open_help_time(cls=other)
    here = make_student("민수")
    there = make_student("지수", cls=other)
    assert here.get("/api/status").json()["help_time"]["active"] is False
    assert there.get("/api/status").json()["help_time"]["active"] is True


# ---------- 학생: 도움 시간에만 돕기와 채팅 ----------


def test_code_and_hints_work_without_help_time(student):
    sub = submit(student)
    finding_id = sub["findings"][0]["id"]
    assert len(student.post(f"/api/findings/{finding_id}/reveal").json()["revealed"]) == 1
    assert student.post(f"/api/threads/{sub['thread_id']}/finish").status_code == 200


def test_cannot_start_helping_outside_help_time(student, make_student, open_help_time):
    submit(student)
    helper = make_student("영희")
    finish_own_code(helper)

    assert helper.get("/api/status").json()["can_help"] is False
    r = helper.post("/api/help/start")
    assert r.status_code == 409 and "도움 시간" in r.json()["detail"]

    open_help_time()
    status = helper.get("/api/status").json()
    assert status["can_help"] is True and status["help_time"]["remaining_seconds"] > 0
    assert helper.post("/api/help/start").status_code == 200


def test_ending_help_time_ends_pairs_and_chat(teacher, classroom, student, make_student, open_help_time):
    open_help_time()
    helper, thread, matched = make_pair(student, make_student)
    url = f"/api/help/{matched['session_id']}/ws"
    with helper.websocket_connect(url) as wa, student.websocket_connect(url) as wb:
        wa.receive_json(), wb.receive_json()
        assert teacher.delete(help_time_url(classroom)).status_code == 200
        # 두 사람 모두에게 시간이 끝났다고 알린다.
        assert wa.receive_json() == {"type": "ended", "reason": "time"}
        assert wb.receive_json() == {"type": "ended", "reason": "time"}

    assert helper.get("/api/status").json()["helping"] is None
    assert student.get("/api/status").json()["helped_by"] is None
    assert helper.get(f"/api/threads/{thread['thread_id']}").status_code == 404  # 친구 코드는 다시 볼 수 없다
    assert student.get("/api/status").json()["open_thread_id"] == thread["thread_id"]  # 고치던 코드는 그대로다


def test_pairs_end_when_time_runs_out(student, make_student, open_help_time):
    open_help_time()
    helper, thread, matched = make_pair(student, make_student)
    let_time_run_out()

    # 화면이 상태를 물어볼 때 짝이 정리된다.
    status = student.get("/api/status").json()
    assert status["help_time"]["active"] is False and status["helped_by"] is None
    assert helper.get("/api/status").json()["helping"] is None
    assert helper.post("/api/help/start").status_code == 409


def test_message_after_time_runs_out_is_not_delivered(student, make_student, open_help_time):
    open_help_time()
    helper, _, matched = make_pair(student, make_student)
    url = f"/api/help/{matched['session_id']}/ws"
    with helper.websocket_connect(url) as wa, student.websocket_connect(url) as wb:
        wa.receive_json(), wb.receive_json()
        let_time_run_out()
        wa.send_json({"body": "3번째 줄을 봐 봐"})
        assert wa.receive_json() == {"type": "ended", "reason": "time"}
        assert wb.receive_json() == {"type": "ended", "reason": "time"}

    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    conn.close()


def test_extending_keeps_the_pair(teacher, classroom, student, make_student, open_help_time):
    open_help_time(1)
    helper, _, matched = make_pair(student, make_student)
    open_help_time(1)  # 1분 추가
    assert helper.get("/api/status").json()["helping"]["session_id"] == matched["session_id"]


def test_same_pair_can_meet_again_in_the_next_help_time(teacher, classroom, student, make_student, open_help_time):
    open_help_time()
    helper, _, first = make_pair(student, make_student)
    teacher.delete(help_time_url(classroom))
    open_help_time()
    again = helper.post("/api/help/start")
    assert again.status_code == 200 and again.json()["session_id"] != first["session_id"]


# ---------- 짝의 이름 가리기 ----------


def set_hide_names(teacher, cls, hide):
    r = teacher.patch(f"/api/teacher/classes/{cls['id']}", json={"hide_names": hide})
    assert r.status_code == 200, r.text
    return r.json()


def test_names_are_shown_by_default_and_teacher_can_hide_them(teacher, classroom, make_teacher):
    assert teacher.get(f"/api/teacher/classes/{classroom['id']}").json()["hide_names"] is False
    assert set_hide_names(teacher, classroom, True)["hide_names"] is True
    assert teacher.get("/api/teacher/classes").json()[0]["hide_names"] is True
    assert teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard").json()["class"]["hide_names"] is True

    stranger = make_teacher("teacher2")
    url = f"/api/teacher/classes/{classroom['id']}"
    assert stranger.patch(url, json={"hide_names": False}).status_code == 404
    assert teacher.patch(url, json={}).status_code == 422


def test_hidden_names_never_reach_the_pair(teacher, classroom, student, make_student, open_help_time):
    set_hide_names(teacher, classroom, True)
    open_help_time()
    thread = submit(student)
    helper = make_student("영희")
    finish_own_code(helper)

    matched = helper.post("/api/help/start")
    assert matched.json()["name"] == "익명"
    assert helper.get("/api/status").json()["helping"]["name"] == "익명"
    assert student.get("/api/status").json()["helped_by"]["name"] == "익명"
    seen = helper.get(f"/api/threads/{thread['thread_id']}")
    assert seen.json()["owner"] == "익명"

    url = f"/api/help/{matched.json()['session_id']}/ws"
    with helper.websocket_connect(url) as wa, student.websocket_connect(url) as wb:
        wa.receive_json(), wb.receive_json()
        wa.send_json({"body": "3번째 줄을 봐 봐"})
        from_helper = wb.receive_json()
        assert from_helper["sender"] == "익명"
        wa.receive_json()
    with student.websocket_connect(url) as wb:
        history = wb.receive_json()

    # 도우미와 도움 받는 학생에게 간 어떤 응답에도 상대의 이름(여기서는 학번과 같다)이 없다.
    for text in (matched.text, seen.text, helper.get("/api/status").text):
        assert "철수" not in text
    for text in (student.get("/api/status").text, str(from_helper), str(history)):
        assert "영희" not in text
    # 내 메시지인지는 이름이 아니라 sender_id 로 가린다.
    assert from_helper["sender_id"] != student.get("/api/me").json()["id"]

    # 교사는 대시보드에서 여전히 누구인지 본다.
    students = teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard").json()["students"]
    assert {s["student_no"] for s in students} == {"철수", "영희"}


def test_showing_names_again(teacher, classroom, student, make_student, open_help_time):
    set_hide_names(teacher, classroom, True)
    open_help_time()
    helper, _, _ = make_pair(student, make_student)
    set_hide_names(teacher, classroom, False)
    assert helper.get("/api/status").json()["helping"]["name"] == "철수"
    assert student.get("/api/status").json()["helped_by"]["name"] == "영희"
