"""교사가 오류별 힌트 문구를 고쳐 쓰는 기능. 교사 계정에 묶여 그 교사의 모든 교실에 적용된다."""

from app.analyzer import catalog

MISSING_COLON = "score = 70\nif score >= 60\n    print('합격')\n"
CASE = "cond.missing_colon"
URL = f"/api/teacher/hints/{CASE}"

CUSTOM = [
    {"label": "살펴보기", "text": "{line}번 줄을 `{keyword}`부터 끝까지 천천히 읽어 보세요."},
    {"label": "선생님 힌트", "text": "지난 시간에 배운 조건문 예시를 공책에서 찾아보세요."},
]


def submit(client, code=MISSING_COLON, thread_id=None):
    body = {"code": code}
    if thread_id is not None:
        body["thread_id"] = thread_id
    r = client.post("/api/submissions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def reveal_all(client, finding):
    revealed = []
    for _ in range(finding["total_steps"]):
        revealed = client.post(f"/api/findings/{finding['id']}/reveal").json()["revealed"]
    return revealed


def hint(client, case_id=CASE):
    return next(c for c in client.get("/api/teacher/hints").json() if c["id"] == case_id)


# ---------- 목록과 기본값 ----------


def test_defaults_are_the_built_in_hints(teacher):
    cases = teacher.get("/api/teacher/hints").json()
    assert [c["id"] for c in cases] == list(catalog.cases())  # 모든 오류 종류가 나온다
    for case in cases:
        built_in = catalog.cases()[case["id"]]
        assert case["customized"] is False
        assert case["steps"] == case["default_steps"] == built_in["steps"]
        assert (case["category"], case["label"]) == (built_in["category"], built_in["label"])

    # 그 오류에서 쓸 수 있는 자리 이름과 뜻을 함께 알려 준다.
    assert [p["name"] for p in hint(teacher)["placeholders"]] == ["line", "keyword"]
    assert all(p["meaning"] for p in hint(teacher)["placeholders"])


def test_hints_require_teacher_login(client, student):
    for c in (client, student):  # 로그인하지 않은 사람도, 학생도 볼 수 없다
        assert c.get("/api/teacher/hints").status_code == 401
        assert c.put(URL, json={"steps": CUSTOM}).status_code == 401
        assert c.delete(URL).status_code == 401


# ---------- 고치기와 되돌리기 ----------


def test_edit_and_reset(teacher):
    saved = teacher.put(URL, json={"steps": CUSTOM})
    assert saved.status_code == 200
    assert saved.json()["steps"] == CUSTOM and saved.json()["customized"] is True
    assert saved.json()["default_steps"] == catalog.cases()[CASE]["steps"]  # 기본 문구는 그대로 남아 있다
    assert hint(teacher)["steps"] == CUSTOM
    assert hint(teacher, "loop.missing_colon")["customized"] is False  # 다른 오류는 그대로다

    # 다시 고치면 덮어쓴다.
    shorter = [{"label": "힌트", "text": "줄 끝을 보세요."}]
    assert teacher.put(URL, json={"steps": shorter}).json()["steps"] == shorter

    reset = teacher.delete(URL)
    assert reset.status_code == 200 and reset.json()["customized"] is False
    assert hint(teacher)["steps"] == catalog.cases()[CASE]["steps"]
    assert teacher.delete(URL).status_code == 200  # 고친 적이 없어도 괜찮다


def test_edit_validates_input(teacher):
    step = {"label": "힌트", "text": "줄 끝을 보세요."}
    bad = [
        {"steps": []},
        {"steps": [step] * 7},
        {"steps": [{"label": "  ", "text": "x"}]},
        {"steps": [{"label": "힌트", "text": "   "}]},
        {"steps": [{"label": "가" * 21, "text": "x"}]},
        {"steps": [{"label": "힌트", "text": "가" * 501}]},
        {},
    ]
    for body in bad:
        assert teacher.put(URL, json=body).status_code == 422, body
    assert hint(teacher)["customized"] is False
    assert teacher.put("/api/teacher/hints/no.such.case", json={"steps": [step]}).status_code == 404
    assert teacher.delete("/api/teacher/hints/no.such.case").status_code == 404

    # 앞뒤 빈칸은 떼고 저장한다.
    saved = teacher.put(URL, json={"steps": [{"label": " 힌트 ", "text": "  줄 끝을 보세요. \n"}]}).json()
    assert saved["steps"] == [step]


# ---------- 학생에게 적용 ----------


def test_students_get_the_teachers_wording(teacher, student):
    teacher.put(URL, json={"steps": CUSTOM})
    sub = submit(student)
    (finding,) = sub["findings"]
    assert finding["total_steps"] == 2 and finding["revealed"] == []
    assert "천천히 읽어" not in str(sub)  # 고친 문구도 열기 전에는 나가지 않는다

    assert reveal_all(student, finding) == [
        {"label": "살펴보기", "text": "2번 줄을 `if`부터 끝까지 천천히 읽어 보세요."},  # 자리가 채워진다
        {"label": "선생님 힌트", "text": "지난 시간에 배운 조건문 예시를 공책에서 찾아보세요."},
    ]

    # 고치지 않은 오류는 기본 문구 그대로다.
    other = submit(student, "for i in range(3)\n    print(i)\n")["findings"][0]
    assert other["total_steps"] == 3
    assert reveal_all(student, other)[0]["label"] == "관찰"


def test_edits_apply_to_every_class_of_the_teacher_but_not_to_other_teachers(
    teacher, make_class, make_teacher, make_student
):
    teacher.put(URL, json={"steps": CUSTOM})
    first = make_student("민수")  # 기본 교실
    second = make_student("지수", cls=make_class("2학년 2반"))  # 같은 교사의 다른 교실
    colleague = make_teacher("teacher2")
    outsider = make_student("현우", cls=make_class("3학년 1반", by=colleague), by=colleague)

    assert submit(first)["findings"][0]["total_steps"] == 2
    assert submit(second)["findings"][0]["total_steps"] == 2
    assert submit(outsider)["findings"][0]["total_steps"] == 3  # 다른 교사의 교실은 기본 문구다
    assert hint(colleague)["customized"] is False


def test_edits_apply_only_to_later_submissions(teacher, student):
    before = submit(student)
    teacher.put(URL, json={"steps": CUSTOM})
    after = submit(student, MISSING_COLON + "\n", before["thread_id"])

    # 이미 제출된 코드의 힌트는 제출할 때의 문구로 남는다.
    assert reveal_all(student, before["findings"][0])[0]["label"] == "관찰"
    assert reveal_all(student, after["findings"][0])[0]["label"] == "살펴보기"

    teacher.delete(URL)
    again = submit(student, MISSING_COLON + "\n\n", before["thread_id"])
    assert again["findings"][0]["total_steps"] == 3  # 되돌리면 다시 기본 문구다


def test_odd_braces_in_custom_text_do_not_break_submissions(teacher, student):
    text = "{line}번 줄: {모르는이름} {keyword.__class__} {} { {char}"
    teacher.put(URL, json={"steps": [{"label": "힌트", "text": text}]})
    revealed = reveal_all(student, submit(student)["findings"][0])
    # 아는 자리만 채우고, 나머지는 쓴 그대로 둔다. 이 오류에서 값이 없는 자리({char})는 비운다.
    assert revealed[0]["text"] == "2번 줄: {모르는이름} {keyword.__class__} {} { "


def test_syntax_notice_does_not_depend_on_the_wording(teacher, student):
    teacher.put(URL, json={"steps": CUSTOM})
    assert submit(student)["syntax_error"] is True
