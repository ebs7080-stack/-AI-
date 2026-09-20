from app import analysis
from app.analysis import Finding, HintStep

CODE = "for i in range(3):\n    print(i)\n"


def test_create_class(client):
    r = client.post("/api/classes", json={"name": "1학년 3반"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["join_code"]) == 6
    assert body["teacher_key"]


def test_create_class_rejects_blank_name(client):
    assert client.post("/api/classes", json={"name": "   "}).status_code == 422


def test_join_sets_session(client, join_code):
    assert client.get("/api/me").status_code == 401
    r = client.post("/api/join", json={"join_code": join_code.lower(), "nickname": " 철수 "})
    assert r.status_code == 200
    assert r.json() == {"nickname": "철수", "class_name": "1학년 3반"}
    assert client.get("/api/me").json() == {"nickname": "철수", "class_name": "1학년 3반"}


def test_join_unknown_code(client):
    r = client.post("/api/join", json={"join_code": "ZZZZZZ", "nickname": "철수"})
    assert r.status_code == 404


def test_join_same_nickname_is_same_student(student, other_client, join_code):
    student.post("/api/submissions", json={"code": CODE})
    r = other_client.post("/api/join", json={"join_code": join_code, "nickname": "철수"})
    assert r.status_code == 200
    assert len(other_client.get("/api/submissions").json()) == 1


def test_nickname_is_case_insensitive(client, join_code, other_client):
    client.post("/api/join", json={"join_code": join_code, "nickname": "Kim"})
    other_client.post("/api/join", json={"join_code": join_code, "nickname": "kim"})
    client.post("/api/submissions", json={"code": CODE})
    assert len(other_client.get("/api/submissions").json()) == 1


def test_logout(student):
    assert student.post("/api/logout").status_code == 204
    assert student.get("/api/me").status_code == 401


def test_submission_requires_login(client):
    assert client.post("/api/submissions", json={"code": CODE}).status_code == 401
    assert client.get("/api/submissions").status_code == 401


def test_blank_code_rejected(student):
    assert student.post("/api/submissions", json={"code": "  \n "}).status_code == 422


def test_code_is_stored_verbatim(student):
    sub = student.post("/api/submissions", json={"code": CODE}).json()
    assert student.get(f"/api/submissions/{sub['id']}").json()["code"] == CODE


def test_hints_are_not_revealed_on_submit(student):
    r = student.post("/api/submissions", json={"code": CODE})
    assert r.status_code == 201
    finding = r.json()["findings"][0]
    assert finding["revealed"] == []
    assert finding["total_steps"] == 3
    # 공개 전 힌트 본문, case_id, 줄 번호가 응답 어디에도 없어야 한다.
    for step in analysis.analyze(CODE)[0].steps:
        assert step.text not in r.text
    assert "stub" not in r.text
    assert "case_id" not in r.text


def test_reveal_one_step_at_a_time(student):
    sub = student.post("/api/submissions", json={"code": CODE}).json()
    finding_id = sub["findings"][0]["id"]
    expected = analysis.analyze(CODE)[0].steps

    for n in range(1, 4):
        finding = student.post(f"/api/findings/{finding_id}/reveal").json()
        assert [s["label"] for s in finding["revealed"]] == [s.label for s in expected[:n]]

    # 모두 공개한 뒤 다시 눌러도 더 늘지 않는다.
    finding = student.post(f"/api/findings/{finding_id}/reveal").json()
    assert len(finding["revealed"]) == finding["total_steps"] == 3

    # 공개한 만큼은 다시 조회해도 유지된다.
    again = student.get(f"/api/submissions/{sub['id']}").json()["findings"][0]
    assert len(again["revealed"]) == 3


def test_cannot_touch_other_students_submission(student, other_client, join_code):
    sub = student.post("/api/submissions", json={"code": CODE}).json()
    finding_id = sub["findings"][0]["id"]

    other_client.post("/api/join", json={"join_code": join_code, "nickname": "영희"})
    assert other_client.get(f"/api/submissions/{sub['id']}").status_code == 404
    assert other_client.post(f"/api/findings/{finding_id}/reveal").status_code == 404
    assert other_client.get("/api/submissions").json() == []


def test_analyzer_is_pluggable(student, monkeypatch):
    fake = [Finding("loop-off-by-one", 2, [HintStep("관찰", "a"), HintStep("힌트", "b")])]
    monkeypatch.setattr(analysis, "analyze", lambda code: fake)
    sub = student.post("/api/submissions", json={"code": CODE}).json()
    assert sub["findings"][0]["total_steps"] == 2


def test_no_findings(student, monkeypatch):
    monkeypatch.setattr(analysis, "analyze", lambda code: [])
    sub = student.post("/api/submissions", json={"code": CODE}).json()
    assert sub["findings"] == []


def test_list_submissions_newest_first(student):
    student.post("/api/submissions", json={"code": "a = 1\n"})
    student.post("/api/submissions", json={"code": "\n\nb = 2\n"})
    items = student.get("/api/submissions").json()
    assert [i["preview"] for i in items] == ["b = 2", "a = 1"]
