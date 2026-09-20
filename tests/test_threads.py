from app import analysis
from app.threads import diff_lines

V1 = "for i in range(3):\nprint(i)\n"
V2 = "for i in range(3):\n    print(i)\n"
V3 = "for i in range(3):\n    print(i)\nprint('끝')\n"


def submit(client, code, thread_id=None):
    body = {"code": code}
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post("/api/submissions", json=body)


def test_diff_lines_marks_added_and_removed():
    ops = diff_lines(V1, V2)
    assert ops == [
        {"op": "equal", "text": "for i in range(3):"},
        {"op": "remove", "text": "print(i)"},
        {"op": "add", "text": "    print(i)"},
    ]


def test_first_submission_starts_open_thread(student):
    r = submit(student, V1)
    assert r.status_code == 201
    assert r.json()["thread_status"] == "open"
    assert student.get("/api/status").json()["open_thread_id"] == r.json()["thread_id"]


def test_fixes_pile_up_next_to_the_original(student):
    first = submit(student, V1).json()
    tid = first["thread_id"]
    second = submit(student, V2, tid).json()
    third = submit(student, V3, tid).json()
    assert second["thread_id"] == third["thread_id"] == tid

    thread = student.get(f"/api/threads/{tid}").json()
    assert [s["code"] for s in thread["submissions"]] == [V1, V2, V3]  # 원래 코드는 그대로 남는다
    assert thread["submissions"][0]["diff"] is None
    assert any(d["op"] == "add" for d in thread["submissions"][1]["diff"])
    assert any(d["op"] == "remove" for d in thread["submissions"][1]["diff"])
    assert thread["is_owner"] is True and thread["owner"] == "철수"


def test_identical_resubmission_is_rejected(student):
    tid = submit(student, V1).json()["thread_id"]
    r = submit(student, V1, tid)
    assert r.status_code == 422
    assert "똑같은" in r.json()["detail"]
    assert len(student.get(f"/api/threads/{tid}").json()["submissions"]) == 1


def test_submission_without_thread_starts_a_new_one_and_drops_the_old(student):
    old = submit(student, V1).json()["thread_id"]
    new = submit(student, V2).json()["thread_id"]
    assert new != old
    threads = {t["id"]: t["status"] for t in student.get("/api/threads").json()}
    assert threads == {old: "dropped", new: "open"}
    assert submit(student, V3, old).status_code == 409  # 그만둔 코드에는 더 쌓을 수 없다


def test_cannot_add_to_someone_elses_thread(student, make_student):
    tid = submit(student, V1).json()["thread_id"]
    other = make_student("영희")
    assert submit(other, V2, tid).status_code == 404
    assert other.get(f"/api/threads/{tid}").status_code == 404
    assert other.post(f"/api/threads/{tid}/finish").status_code == 404


def test_thread_is_fixed_when_no_findings_remain(student, monkeypatch):
    tid = submit(student, V1).json()["thread_id"]
    monkeypatch.setattr(analysis, "analyze", lambda code: [])
    r = submit(student, V2, tid).json()
    assert r["thread_status"] == "fixed"
    assert student.get("/api/status").json()["open_thread_id"] is None
    assert submit(student, V3, tid).status_code == 409


def test_finish_thread(student):
    tid = submit(student, V1).json()["thread_id"]
    r = student.post(f"/api/threads/{tid}/finish")
    assert r.status_code == 200 and r.json()["status"] == "fixed"
    assert student.post(f"/api/threads/{tid}/finish").json()["status"] == "fixed"  # 다시 눌러도 그대로


def test_cannot_finish_a_dropped_thread(student):
    old = submit(student, V1).json()["thread_id"]
    submit(student, V2)
    assert student.post(f"/api/threads/{old}/finish").status_code == 409


def test_threads_list_newest_first_with_counts(student):
    a = submit(student, "a = 1\n").json()["thread_id"]
    submit(student, "a = 2\n", a)
    b = submit(student, "\n\nb = 1\n").json()["thread_id"]
    items = student.get("/api/threads").json()
    assert [i["id"] for i in items] == [b, a]
    assert items[0]["preview"] == "b = 1" and items[0]["submission_count"] == 1
    assert items[1]["preview"] == "a = 1" and items[1]["submission_count"] == 2


def test_thread_detail_does_not_leak_hidden_hints(student):
    tid = submit(student, V1).json()["thread_id"]
    text = student.get(f"/api/threads/{tid}").text
    for step in analysis.analyze(V1)[0].steps:
        assert step.text not in text
    assert "case_id" not in text and "stub" not in text


def test_threads_require_login(client):
    for path in ("/api/status", "/api/threads", "/api/threads/1"):
        assert client.get(path).status_code == 401
    assert client.post("/api/help/start").status_code == 401
