"""문법 오류를 case 로 나누고 단계별 힌트를 만드는 분석기 1단계.

파이썬 버전을 올리면 SyntaxError 문구가 바뀔 수 있다. 그때 이 파일의 예시가 먼저 실패해서 알려 준다.
"""

import re

import pytest

from app import analysis
from app.analyzer import catalog, syntax

# (학생 코드, 기대하는 case, 오류가 난 줄)
BAD = [
    # 콜론 빠뜨리기: 줄의 첫 단어로 조건문/반복문/함수를 가른다.
    ("score = 70\nif score >= 60\n    print('합격')\n", "cond.missing_colon", 2),
    ("if x:\n    pass\nelif x > 1\n    pass\n", "cond.missing_colon", 3),
    ("if x:\n    pass\nelse\n    pass\n", "cond.missing_colon", 3),
    ("for i in range(3)\n    print(i)\n", "loop.missing_colon", 1),
    ("while x < 3\n    x += 1\n", "loop.missing_colon", 1),
    ("def f(a)\n    return a\n", "func.missing_colon", 1),
    # else if 는 콜론 빠뜨리기와 메시지가 같아서 먼저 봐야 한다.
    ("if x:\n    pass\nelse if x == 2:\n    pass\n", "cond.else_if", 3),
    ("def f:\n    pass\n", "func.def_missing_paren", 1),
    ("def f\n    pass\n", "func.def_missing_paren", 1),
    ("if x = 1:\n    pass\n", "cond.assign_in_if", 1),
    ("while x = 1:\n    pass\n", "cond.assign_in_if", 1),
    # 본문 들여쓰기
    ("if x == 1:\nprint(x)\n", "cond.body_not_indented", 2),
    ("if x:\n    pass\nelif y:\nprint(1)\n", "cond.body_not_indented", 4),
    ("if x:\n    pass\nelse:\nprint(1)\n", "cond.body_not_indented", 4),
    ("for i in range(3):\nprint(i)\n", "loop.body_not_indented", 2),
    ("while True:\nprint(1)\n", "loop.body_not_indented", 2),
    ("for i in range(3):\nfor j in range(3):\n    print(i, j)\n", "nested.inner_same_indent", 2),
    ("def f():\nreturn 1\n", "func.body_not_indented", 2),
    ("if x:\n        print(1)\n    else:\n        print(2)\n", "cond.else_misaligned", 3),
    # 변수 이름
    ("1st = 3\n", "var.name_starts_with_digit", 1),
    ("my name = 3\n", "var.name_with_space", 1),
    # 오류case.txt 에는 없지만 초보자에게 흔한 것들
    ("print((1 + 2)\n", "syntax.unclosed_bracket", 1),
    ("x = [1, 2\nprint(x)\n", "syntax.unclosed_bracket", 1),
    ("print('hello)\n", "syntax.unclosed_string", 1),
    ("s = '''abc\n", "syntax.unclosed_string", 1),
    ("print\uff081\uff09\n", "syntax.fullwidth_char", 1),
    ("print(\u201chi\u201d)\n", "syntax.fullwidth_char", 1),
    # 어느 case 인지 확실하지 않으면 억지로 넣지 않는다.
    # 들여쓰기
    ("x = 1\n    y = 2\n", "syntax.unexpected_indent", 2),
    ("if x == 1:\n    print(1)\n  print(2)\n", "syntax.indent_mismatch", 3),
    ("x = 3 +\n", "syntax.unknown", 1),
    ("class A\n    pass\n", "syntax.unknown", 1),
    ("print(x = 1 if y)\n", "syntax.unknown", 1),
    ("return x = 3\n", "syntax.unknown", 1),
    ("for i in range(3):\n\tprint(i)\n        print(2)\n", "syntax.unknown", 3),
]

# 문법에 맞는 코드. 하나도 잡으면 안 된다 (오탐 방지).
GOOD = [
    "",
    "x = 1\n",
    "score = int(input())\nif score >= 60:\n    print('합격')\nelif score >= 40:\n    print('재시험')\nelse:\n    print('불합격')\n",
    "for i in range(3):\n    for j in range(3):\n        print(i, j)\n",
    "def f(a, b=1):\n    return a + b\n\nprint(f(1))\n",
    "while True:\n    break\n",
    "이름 = '철수'\nprint(이름)\n",
    "x = (1 +\n     2)\n",
    "s = '''여러 줄\n문자열'''\n",
    "if (n := 3) == 3:\n    print(n)\n",
    # 문법은 맞고 실행하면 틀리는 코드: 1단계가 볼 일이 아니다.
    "print(undefined_name)\n",
    "x = '1' + 1\n",
]


@pytest.mark.parametrize("code, case_id, line", BAD)
def test_classify_bad_code(code, case_id, line):
    match = syntax.classify(code)
    assert match is not None
    assert (match.case_id, match.line) == (case_id, line)


@pytest.mark.parametrize("code", GOOD)
def test_good_code_is_not_flagged(code):
    assert syntax.classify(code) is None
    # 실행하면 틀리는 코드는 논리 규칙(test_logic.py)이 잡을 수 있지만, 문법 오류로 나오지는 않는다.
    assert not any(analysis.is_syntax_case(f.case_id) for f in analysis.analyze(code))


def test_code_is_never_executed(tmp_path):
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w').close()\nif x\n"
    assert analysis.analyze(code)[0].case_id == "cond.missing_colon"
    assert analysis.analyze(f"open({str(marker)!r}, 'w').close()\n") == []
    assert not marker.exists()


def test_very_deep_nesting_does_not_break_the_analyzer():
    # 파이썬이 읽기를 포기할 만큼 깊은 중첩도 서버 오류가 아니라 일반 문법 힌트로 끝난다.
    (finding,) = analysis.analyze("(" * 5000 + ")" * 5000)
    assert finding.case_id == "syntax.unknown"


def test_null_byte_is_a_generic_syntax_error():
    finding = analysis.analyze("x = 1\n\x00\n")[0]
    assert finding.case_id == "syntax.unknown" and finding.line is not None


# ---------- 단계별 힌트 ----------


def test_every_syntax_case_in_the_table_has_a_bad_example():
    covered = {case_id for _, case_id, _ in BAD}
    assert covered == {cid for cid, case in catalog.cases().items() if case["method"] == "syntax"}


STEP_ORDER = ["관찰", "의심 지점", "힌트", "추가 힌트"]


@pytest.mark.parametrize("code, case_id, line", BAD)
def test_feedback_steps(code, case_id, line):
    (finding,) = analysis.analyze(code)  # 문법 오류는 언제나 하나만 알려 준다
    assert (finding.case_id, finding.line) == (case_id, line)
    # 단계 수는 case 마다 다를 수 있다. 순서는 관찰 → 의심 지점 → 힌트 → 추가 힌트이고,
    # '의심 지점'과 '힌트'는 꼭 있다.
    labels = [s.label for s in finding.steps]
    assert labels == [label for label in STEP_ORDER if label in labels]
    assert "의심 지점" in labels and "힌트" in labels

    # 줄 번호는 '의심 지점'에서 처음 나온다.
    by_label = {s.label: s.text for s in finding.steps}
    assert not re.search(r"\d+번 줄", by_label.get("관찰", ""))
    assert f"{line}번 줄" in by_label["의심 지점"]
    for text in by_label.values():
        assert text.strip()
        assert "{" not in text and "}" not in text  # 채우지 못한 자리가 남지 않는다
        assert "``" not in text  # 빈 값이 들어간 자리도 없다
        # 학생에게는 파이썬의 영어 오류 문구를 그대로 보여 주지 않는다.
        assert "SyntaxError" not in text and "expected" not in text and "invalid" not in text


def test_steps_mention_what_was_found():
    steps = analysis.analyze("score = 70\nif score >= 60\n    print('합격')\n")[0].steps
    assert steps[0].text.startswith("파이썬이 `if`로 시작하는 줄을 읽다가 멈췄어요.")
    assert steps[1].text == "2번 줄의 맨 끝을 보세요. 기호 하나가 빠져 있어요."

    steps = analysis.analyze("x = 0\nfor i in range(3):\nprint(i)\n")[0].steps
    assert "2번 줄 `for` 바로 아래에 있는 3번 줄" in steps[1].text

    steps = analysis.analyze("print\uff081\uff09\n")[0].steps
    assert "`\uff08`" in steps[1].text


def test_step_count_varies_by_case():
    count = lambda code: len(analysis.analyze(code)[0].steps)
    assert count("x = 3 +\n") == 2  # 어떤 오류인지 모를 때는 볼 곳과 확인할 것만 알려 준다
    assert count("if x > 1\n    pass\n") == 3
    assert count("my name = 3\n") == 4  # 추가 힌트가 있는 case


@pytest.mark.parametrize(
    "code, answer",
    [
        ("if x > 1\n    pass\n", ":"),
        ("for i in range(3)\n    pass\n", ":"),
        ("def f(a)\n    pass\n", ":"),
        ("if x:\n    pass\nelse if x == 2:\n    pass\n", "elif"),
        ("if x = 1:\n    pass\n", "=="),
        ("def f:\n    pass\n", "()"),
        ("print((1 + 2)\n", ")"),
    ],
)
def test_hints_do_not_give_the_answer(code, answer):
    # 무엇을 떠올려야 하는지만 묻고, 고칠 기호나 단어를 직접 알려 주지 않는다 (FR-1.4).
    for step in analysis.analyze(code)[0].steps:
        assert answer not in step.text


def test_case_labels_for_the_teacher_dashboard():
    assert analysis.case_label("cond.missing_colon") == "[조건문] 조건문 뒤에 콜론을 빠뜨리기"
    assert analysis.case_label("stub").startswith("임시 분석 결과")  # 분석기를 붙이기 전의 기록
    assert analysis.case_label("no.such.case") == "no.such.case"


# ---------- 웹으로 제출했을 때 ----------


def test_submission_gets_syntax_feedback_one_step_at_a_time(student):
    r = student.post("/api/submissions", json={"code": "score = 70\nif score >= 60\n    print('합격')\n"})
    assert r.status_code == 201
    (finding,) = r.json()["findings"]
    assert finding["total_steps"] == 3 and finding["revealed"] == []
    assert "2번 줄" not in r.text  # 열기 전에는 줄 번호도 나가지 않는다
    # 문법 오류라서 실행되지 않는다는 사실은 바로 알려 준다 (어떤 오류인지는 알려 주지 않는다).
    assert r.json()["syntax_error"] is True and "cond.missing_colon" not in r.text

    labels = []
    for _ in range(3):
        revealed = student.post(f"/api/findings/{finding['id']}/reveal").json()["revealed"]
        labels = [s["label"] for s in revealed]
    assert labels == ["관찰", "의심 지점", "힌트"]
    assert revealed[1]["text"] == "2번 줄의 맨 끝을 보세요. 기호 하나가 빠져 있어요."


def test_fixing_the_syntax_error_clears_the_feedback(student):
    first = student.post("/api/submissions", json={"code": "x = 3\nif x > 1\n    print(x)\n"}).json()
    assert len(first["findings"]) == 1
    fixed = student.post(
        "/api/submissions", json={"code": "x = 3\nif x > 1:\n    print(x)\n", "thread_id": first["thread_id"]}
    ).json()
    assert first["syntax_error"] is True and fixed["syntax_error"] is False
    thread = student.get(f"/api/threads/{first['thread_id']}").json()
    assert [s["syntax_error"] for s in thread["submissions"]] == [True, False]
    assert fixed["findings"] == []
    assert fixed["thread_status"] == "open"  # 찾은 것이 없어도 맞다는 보장은 없으므로 다 고쳤는지는 학생이 정한다
