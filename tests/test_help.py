import pytest
from starlette.websockets import WebSocketDisconnect

from app import analysis

CODE = "for i in range(3):\nprint(i)\n"


def submit(client, code=CODE, thread_id=None):
    body = {"code": code}
    if thread_id is not None:
        body["thread_id"] = thread_id
    r = client.post("/api/submissions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def finish_own_code(client, code="a = 1\n"):
    """자기 코드를 하나 완성해서 도우미가 될 자격을 얻는다."""
    tid = submit(client, code)["thread_id"]
    assert client.post(f"/api/threads/{tid}/finish").status_code == 200


@pytest.fixture(autouse=True)
def help_time(open_help_time):
    """이 파일의 테스트는 도움 시간이 열린 교실에서 시작한다. 닫힌 경우는 test_help_time.py 가 본다."""
    open_help_time()


@pytest.fixture
def pair(student, make_student):
    """철수(도움이 필요함, 코드를 고치는 중)와 그를 돕는 영희."""
    b = student
    thread = submit(b)
    helper = make_student("영희")
    finish_own_code(helper)
    matched = helper.post("/api/help/start")
    assert matched.status_code == 200, matched.text
    return b, helper, thread, matched.json()


def test_can_help_only_after_finishing_own_code(student):
    assert student.get("/api/status").json()["can_help"] is False
    tid = submit(student)["thread_id"]
    assert student.get("/api/status").json()["can_help"] is False  # 아직 고치는 중
    student.post(f"/api/threads/{tid}/finish")
    assert student.get("/api/status").json()["can_help"] is True


def test_start_requires_finished_code(student):
    assert student.post("/api/help/start").status_code == 409  # 제출한 적도 없다
    submit(student)
    r = student.post("/api/help/start")
    assert r.status_code == 409 and "다 고친" in r.json()["detail"]


def test_start_without_anyone_to_help(student):
    finish_own_code(student)
    r = student.post("/api/help/start")
    assert r.status_code == 404 and "없어요" in r.json()["detail"]


def test_start_matches_a_classmate(pair):
    b, helper, thread, matched = pair
    assert matched["name"] == "철수" and matched["thread_id"] == thread["thread_id"]
    assert helper.get("/api/status").json()["helping"]["session_id"] == matched["session_id"]
    helped_by = b.get("/api/status").json()["helped_by"]
    assert helped_by == {"session_id": matched["session_id"], "name": "영희"}


def test_never_matches_another_class(student, make_student, make_class, open_help_time):
    submit(student)
    other_class = make_class("2학년 1반")
    open_help_time(cls=other_class)
    outsider = make_student("영희", cls=other_class)
    finish_own_code(outsider)
    assert outsider.post("/api/help/start").status_code == 404


def test_one_helper_per_thread_and_rematch_is_allowed(pair, make_student):
    b, helper, thread, matched = pair
    third = make_student("민수")
    finish_own_code(third)
    assert third.post("/api/help/start").status_code == 404  # 이미 도우미가 있다

    assert helper.post(f"/api/help/{matched['session_id']}/end").status_code == 204
    # 매칭은 완전히 무작위라, 방금 도운 친구와도 다시 짝이 될 수 있다.
    again = helper.post("/api/help/start")
    assert again.status_code == 200 and again.json()["session_id"] != matched["session_id"]
    assert again.json()["thread_id"] == thread["thread_id"]


def test_cannot_help_twice_or_while_own_code_is_open(pair):
    b, helper, *_ = pair
    assert helper.post("/api/help/start").status_code == 409  # 이미 돕는 중
    submit_attempt = b.post("/api/help/start")
    assert submit_attempt.status_code == 409  # 철수는 고치는 중


def test_double_click_while_matching_is_409_not_404(pair, make_student, monkeypatch):
    """버튼을 두 번 눌러 두 요청이 겹치면, 늦은 요청은 앞의 검사를 지난 뒤 짝 만들기에서 막힌다."""
    from app import collab

    _, helper, _, _ = pair
    submit(make_student("민수"))  # 도움이 필요한 다른 친구가 있어 후보는 남아 있다
    real = collab._helping_row
    calls = []

    def first_check_misses(db, student_id):
        calls.append(student_id)
        return None if len(calls) == 1 else real(db, student_id)  # 첫 검사 때는 앞 요청이 아직 안 끝났다

    monkeypatch.setattr(collab, "_helping_row", first_check_misses)
    r = helper.post("/api/help/start")
    assert r.status_code == 409 and "도와주는 중" in r.json()["detail"]


def test_helper_can_view_the_thread_only_while_matched(pair, make_student):
    b, helper, thread, matched = pair
    tid = thread["thread_id"]
    assert helper.get(f"/api/threads/{tid}").json()["owner"] == "철수"
    assert make_student("민수").get(f"/api/threads/{tid}").status_code == 404

    helper.post(f"/api/help/{matched['session_id']}/end")
    assert helper.get(f"/api/threads/{tid}").status_code == 404


def test_helper_cannot_submit_own_code_while_helping(pair):
    _, helper, *_ = pair
    r = helper.post("/api/submissions", json={"code": "x = 1\n"})
    assert r.status_code == 409 and "도와주는 중" in r.json()["detail"]


def test_hints_are_opened_together_one_step_at_a_time(pair):
    b, helper, thread, _ = pair
    finding_id = thread["findings"][0]["id"]
    expected = analysis.analyze(CODE)[0].steps

    assert len(b.post(f"/api/findings/{finding_id}/reveal").json()["revealed"]) == 1
    seen_by_helper = helper.get(f"/api/threads/{thread['thread_id']}").json()["submissions"][0]["findings"][0]
    assert [s["label"] for s in seen_by_helper["revealed"]] == [expected[0].label]  # 도우미도 같은 단계까지만

    assert len(helper.post(f"/api/findings/{finding_id}/reveal").json()["revealed"]) == 2  # 도우미도 열 수 있다
    seen_by_owner = b.get(f"/api/threads/{thread['thread_id']}").json()["submissions"][0]["findings"][0]
    assert len(seen_by_owner["revealed"]) == 2
    hidden = expected[2].text
    assert hidden not in helper.get(f"/api/threads/{thread['thread_id']}").text  # 아직 안 열린 단계는 어디에도 없다


def test_unmatched_student_cannot_reveal_hints(pair, make_student):
    _, _, thread, _ = pair
    stranger = make_student("민수")
    assert stranger.post(f"/api/findings/{thread['findings'][0]['id']}/reveal").status_code == 404


def test_either_side_can_end_the_pair(pair):
    b, helper, _, matched = pair
    assert b.post(f"/api/help/{matched['session_id']}/end").status_code == 204
    assert b.get("/api/status").json()["helped_by"] is None
    assert helper.get("/api/status").json()["helping"] is None
    assert helper.post(f"/api/help/{matched['session_id']}/end").status_code == 204  # 다시 눌러도 괜찮다


def test_strangers_cannot_end_a_pair(pair, make_student):
    _, _, _, matched = pair
    assert make_student("민수").post(f"/api/help/{matched['session_id']}/end").status_code == 404


def test_finishing_the_thread_ends_the_pair(pair):
    b, helper, thread, matched = pair
    assert b.post(f"/api/threads/{thread['thread_id']}/finish").status_code == 200
    assert helper.get("/api/status").json()["helping"] is None
    assert helper.get(f"/api/threads/{thread['thread_id']}").status_code == 404


def test_starting_a_new_code_ends_the_pair(pair):
    b, helper, *_ = pair
    submit(b, "z = 1\n")
    assert helper.get("/api/status").json()["helping"] is None


# ---------- 채팅 (WebSocket) ----------


def test_chat_delivers_messages_to_both_and_keeps_history(pair):
    b, helper, _, matched = pair
    url = f"/api/help/{matched['session_id']}/ws"
    with helper.websocket_connect(url) as wa, b.websocket_connect(url) as wb:
        assert wa.receive_json() == {"type": "history", "messages": []}
        assert wb.receive_json() == {"type": "history", "messages": []}

        wa.send_json({"body": "  3번째 줄을 봐 봐  "})
        for ws in (wa, wb):
            msg = ws.receive_json()
            assert msg["type"] == "message" and msg["sender"] == "영희" and msg["body"] == "3번째 줄을 봐 봐"

        wb.send_json({"body": "들여쓰기가 문제야?"})
        assert wa.receive_json()["sender"] == "철수"
        wb.receive_json()

    with b.websocket_connect(url) as wb:  # 다시 들어오면 지난 대화가 보인다
        history = wb.receive_json()["messages"]
        assert [m["body"] for m in history] == ["3번째 줄을 봐 봐", "들여쓰기가 문제야?"]


def test_chat_blocks_pasted_answers(pair):
    b, helper, _, matched = pair
    url = f"/api/help/{matched['session_id']}/ws"
    with helper.websocket_connect(url) as wa, b.websocket_connect(url) as wb:
        wa.receive_json(), wb.receive_json()

        wa.send_json({"body": "for i in range(3):\n    print(i)\nprint('끝')"})
        blocked = wa.receive_json()
        assert blocked["type"] == "blocked" and "말로 설명" in blocked["reason"]

        wa.send_json({"body": "들여쓰기를 확인해 봐"})  # 막힌 메시지는 상대에게 가지 않았다
        assert wb.receive_json()["body"] == "들여쓰기를 확인해 봐"
        assert wa.receive_json()["type"] == "message"


def test_chat_rejects_too_long_and_ignores_blank(pair):
    b, helper, _, matched = pair
    with helper.websocket_connect(f"/api/help/{matched['session_id']}/ws") as wa:
        wa.receive_json()
        wa.send_json({"body": "   "})
        wa.send_json({"body": "가" * 501})
        assert wa.receive_json()["type"] == "blocked"


def test_chat_is_only_for_the_pair(pair, make_student, client):
    _, _, _, matched = pair
    url = f"/api/help/{matched['session_id']}/ws"
    for outsider in (make_student("민수"), client.__class__(client.app)):
        with pytest.raises(WebSocketDisconnect):
            with outsider.websocket_connect(url):
                pass


def test_chat_closed_after_the_pair_ends(pair):
    b, helper, _, matched = pair
    helper.post(f"/api/help/{matched['session_id']}/end")
    with pytest.raises(WebSocketDisconnect):
        with b.websocket_connect(f"/api/help/{matched['session_id']}/ws"):
            pass


def test_chat_rejects_cross_site_origin(pair):
    b, _, _, matched = pair
    with pytest.raises(WebSocketDisconnect):
        with b.websocket_connect(
            f"/api/help/{matched['session_id']}/ws", headers={"origin": "http://evil.example"}
        ):
            pass


def test_events_reach_the_other_side(pair):
    b, helper, thread, matched = pair
    url = f"/api/help/{matched['session_id']}/ws"
    with helper.websocket_connect(url) as wa:
        wa.receive_json()

        b.post(f"/api/findings/{thread['findings'][0]['id']}/reveal")
        assert wa.receive_json() == {"type": "hint"}

        submit(b, "for i in range(3):\n    print(i)\n", thread["thread_id"])
        assert wa.receive_json() == {"type": "submission"}

        b.post(f"/api/threads/{thread['thread_id']}/finish")
        assert wa.receive_json() == {"type": "ended", "reason": "fixed"}
