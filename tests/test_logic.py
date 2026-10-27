"""논리 오류 규칙 (app/analyzer/rules/). 잡아야 할 코드와, 비슷하지만 맞는 코드를 함께 확인한다."""

import re

import pytest

from app import analysis
from app.analyzer import catalog, rules


@pytest.fixture(autouse=True)
def rules_do_not_fail(caplog, request):
    """규칙 하나가 예외를 내면 run() 이 로그만 남기고 넘어가서, 테스트가 그 규칙의 고장을 놓칠 수 있다."""
    yield
    if request.node.get_closest_marker("allow_rule_errors") is None:
        assert "논리 규칙" not in caplog.text

# (코드, case_id, 의심 줄). 한 코드에는 그 case 하나만 걸리게 쓴다.
BAD = [
    # ---------- 변수 ----------
    ("sum = 0\nfor n in [1, 2]:\n    sum += n\nprint(sum([1, 2]))\n", "var.builtin_shadowed", 1),
    ("age = 15\nprint('나이: ' + age)\n", "var.str_plus_number", 2),
    ("print('합계: ' + len([1, 2]))\n", "var.str_plus_number", 1),
    ("a = input()\nprint(a + 1)\n", "var.input_not_converted", 2),
    ("a = input()\nprint(a * 2)\n", "var.input_not_converted", 2),
    ("total = 0\na = input()\ntotal += a\nprint(total)\n", "var.input_not_converted", 3),
    ("total = 0\nfor i in range(3):\n    n = input()\n    total = total + n\n", "var.input_not_converted", 4),
    ("s = [90, 80]\navg = sum(s) / len(s)\nprint('평균은 ' + avg)\n", "var.str_plus_number", 3),
    ("x = 1\nx + 1\nprint(x)\n", "var.result_not_stored", 2),
    ("x = 1\nx == 5\nprint(x)\n", "var.eq_as_assign", 2),
    ("a = 1\nb = 2\na = b\nb = a\nprint(a, b)\n", "var.swap_without_temp", 3),
    ("name = '철수'\nprint('name')\n", "var.quoted_variable", 2),
    # ---------- 조건문 ----------
    ("a = input()\nif a == 3:\n    print('셋')\n", "cond.input_str_compare", 2),
    ("x = int(input())\nif x == 1 or 2:\n    print('1 또는 2')\n", "cond.or_constant", 2),
    ("x = int(input())\nwhile x == 1 or 'y':\n    x = int(input())\n", "cond.or_constant", 2),
    ("s = int(input())\nif s >= 60:\n    g = 'D'\nelif s >= 90:\n    g = 'A'\nprint(g)\n",
     "cond.wide_condition_first", 4),
    ("s = int(input())\nif s < 10:\n    print('a')\nelif s < 5:\n    print('b')\n", "cond.wide_condition_first", 4),
    ("s = int(input())\nif s >= 60:\n    print('a')\nelif s > 60:\n    print('b')\n", "cond.wide_condition_first", 4),
    # ---------- 반복문 ----------
    ("while True:\n    x = input()\n    print(x)\n", "loop.while_true_no_break", 1),
    ("while True:\n    for i in range(3):\n        break\n", "loop.while_true_no_break", 1),
    ("i = 0\nwhile i < 5:\n    print(i)\n", "loop.while_no_update", 2),
    ("i = 0\na = [1, 2]\nwhile i < len(a):\n    print(a[i])\n", "loop.while_no_update", 3),
    ("count = 0\nfor x in [1, -2, 3]:\n    if x > 0:\n        count = 1\nprint(count)\n", "loop.count_set_to_one", 4),
    ("scores = [90, 80]\ntotal = 0\nfor i in scores:\n    total += scores[i]\nprint(total)\n", "loop.value_as_index", 4),
    ("a = [1, -2, 3]\nfor x in a:\n    if x < 0:\n        a.remove(x)\nprint(a)\n", "loop.modify_while_iterating", 4),
    ("a = [1, 2]\nfor x in a:\n    a.append(x)\n", "loop.modify_while_iterating", 3),
    ("def is_prime(n):\n    for i in range(2, n):\n        if n % i == 0:\n            return False\n"
     "        else:\n            return True\n\nprint(is_prime(9))\n", "loop.return_in_loop", 3),
    # ---------- 중첩 반복문 ----------
    ("for i in range(3):\n    for i in range(4):\n        print(i)\n", "nested.same_loop_var", 2),
    ("board = [[0] * 3] * 4\nboard[0][0] = 1\nprint(board)\n", "nested.shared_rows", 1),
    # ---------- 함수 ----------
    ("def area(w, h):\n    return w * h\n\nprint(area(3))\n", "func.wrong_arg_count", 4),
    ("def hi(name):\n    print(name)\n\nhi('a', 'b')\n", "func.wrong_arg_count", 4),
    ("def mm(a):\n    return min(a), max(a)\n\nx, y, z = mm([1, 2])\nprint(x, y, z)\n", "func.unpack_mismatch", 4),
    ("count = 0\ndef add():\n    count += 1\n\nadd()\nprint(count)\n", "func.global_without_decl", 3),
    ("count = 0\ndef add():\n    count = count + 1\n\nadd()\n", "func.global_without_decl", 3),
    ("def fact(n):\n    return n * fact(n - 1)\n\nprint(fact(3))\n", "func.recursion_no_base", 1),
    ("def add(a, b):\n    print(a + b)\n\ntotal = add(3, 4)\nprint(total)\n", "func.print_not_return", 4),
    ("def add(a, b):\n    print(a + b)\n\nprint(add(3, 4))\n", "func.print_not_return", 4),
    ("def setup():\n    x = 1\n\nresult = setup()\nprint(result)\n", "func.none_used", 4),
    ("def grade(s):\n    if s >= 90:\n        return 'A'\n    elif s >= 80:\n        return 'B'\n\nprint(grade(50))\n",
     "func.missing_return", 2),
    ("def add(a, b):\n    return a + b\n\nadd(1, 2)\n", "func.return_ignored", 4),
    ("def hello():\n    print('hi')\n\nhello\n", "func.missing_call_parens", 4),
    ("print\n", "func.missing_call_parens", 1),
    ("def f(x):\n    return x * 2\n    print('끝')\n\nprint(f(1))\n", "func.unreachable", 3),
    ("def f():\n    return 1\n\ndef f():\n    return 2\n\nprint(f())\n", "func.defined_twice", 4),
    ("def total(a):\n    return sum(a)\n\ntotal = 0\nprint(total([1]))\n", "func.name_overwritten", 4),
]

# 비슷하게 생겼지만 맞는 코드. 아무것도 잡히면 안 된다 (오탐 방지).
GOOD = [
    "total = 0\nfor n in [1, 2]:\n    total += n\nprint(sum([1, 2]), total)\n",
    "def f(list):\n    return len(list)\n\nprint(list(range(3)), f([1]))\n",  # 함수 안의 매개변수 이름
    "age = 15\nprint('나이: ' + str(age))\nprint('나이:', age)\n",
    "name = input()\nprint(name + '님')\nprint('-' * 20)\n",
    "a = int(input())\nprint(a + 1)\nif a == 3:\n    print('셋')\n",
    "a = input()\na = int(a)\nprint(a + 1)\n",  # 나중에 숫자로 바꿨다
    "a = input()\nif a == 'y':\n    print('예')\nif len(a) > 3:\n    print('길다')\n",
    "x = 1\nx = x + 1\ny = x == 2\nprint(x, y)\n",
    "a = 1\nb = 2\na, b = b, a\nt = a\na = b\nb = t\nprint(a, b)\n",
    "print('name')\nname = '철수'\n",  # 변수보다 먼저 쓴 글자는 그냥 글자다
    "x = int(input())\nif x == 1 or x == 2:\n    print('1 또는 2')\nname = input() or '익명'\n",
    "s = int(input())\nif s >= 90:\n    g = 'A'\nelif s >= 60:\n    g = 'D'\nelse:\n    g = 'F'\nprint(g)\n",
    "s = int(input())\nif s > 60:\n    print('a')\nelif s >= 60:\n    print('b')\n",  # 60 은 뒤 조건에 닿는다
    "while True:\n    x = input()\n    if x == 'q':\n        break\n",
    "def f():\n    while True:\n        x = input()\n        if x:\n            return x\n\nprint(f())\n",
    "i = 0\nwhile i < 5:\n    print(i)\n    i += 1\n",
    "a = [1, 2]\nwhile len(a) > 0:\n    a.pop()\n",
    "x = ''\nwhile x != 'q':\n    x = input()\n",
    "count = 0\nfor x in [1, -2, 3]:\n    if x > 0:\n        count += 1\nprint(count)\n",
    "found = 0\nfor x in [1, 2]:\n    if x == 2:\n        found = 1\nprint(found)\n",  # 표시(flag)는 1 로 둬도 된다
    "scores = [90, 80]\nfor i in range(len(scores)):\n    print(scores[i])\nfor s in scores:\n    print(s)\n",
    "d = {'a': 1}\nfor k in d:\n    print(d[k])\n",  # 사전을 돌 때는 d[k] 가 맞다
    "a = [1, -2, 3]\nfor x in a[:]:\n    if x < 0:\n        a.remove(x)\nb = [x for x in a if x > 0]\nprint(a, b)\n",
    "a = [1, 2, 3]\nfor x in a:\n    if x == 2:\n        a.remove(x)\n        break\n",
    "def is_prime(n):\n    for i in range(2, n):\n        if n % i == 0:\n            return False\n    return True\n\nprint(is_prime(9))\n",
    "def first_positive(a):\n    for x in a:\n        if x <= 0:\n            continue\n        return x\n\nprint(first_positive([1]))\n",
    "for i in range(3):\n    for j in range(4):\n        print(i, j)\nfor _ in range(2):\n    for _ in range(2):\n        pass\n",
    "board = [[0] * 3 for _ in range(4)]\nrow = [0] * 3\nprint(board, row)\n",
    "def area(w, h=1):\n    return w * h\n\nprint(area(3), area(3, 4), area(w=3))\n",
    "def mm(a):\n    return min(a), max(a)\n\nx, y = mm([1, 2])\nlo, *rest = mm([1, 2])\nprint(x, y, lo, rest)\n",
    "count = 0\ndef add():\n    global count\n    count += 1\n\ndef local():\n    count = 0\n    count += 1\n    return count\n\nadd()\nprint(local())\n",
    "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n\nprint(fact(3))\n",
    "def add(a, b):\n    return a + b\n\ntotal = add(3, 4)\nprint(add(1, 2), total)\n",
    "def show(a):\n    print(a)\n\nshow(3)\n",
    "def grade(s):\n    if s >= 90:\n        return 'A'\n    elif s >= 80:\n        return 'B'\n    else:\n        return 'C'\n\nprint(grade(50))\n",
    "def grade(s):\n    if s >= 90:\n        return 'A'\n    return 'B'\n\nprint(grade(50))\n",
    "def find(a, t):\n    for i in range(len(a)):\n        if a[i] == t:\n            return i\n\nprint(find([1], 1))\n",
    "def add_item(a, x):\n    a.append(x)\n    return len(a)\n\nitems = []\nadd_item(items, 1)\n",  # 리스트를 바꾸는 함수
    "def hello():\n    print('hi')\n\nhello()\nf = hello\nf()\n",
    "def f(x):\n    if x:\n        return 1\n    return 2\n\nprint(f(1))\n",
    "def total(a):\n    return sum(a)\n\nresult = total([1])\nprint(result)\n",
    # 표준 라이브러리에서 찾은, 일부러 그렇게 쓴 맞는 코드
    "def first(a):\n    for x in a:\n        return x\n\nprint(first([1]))\n",  # 첫 번째 것만 꺼내기
    "def gen():\n    while True:\n        yield 1\n",
    "def drain(s):\n    try:\n        while True:\n            s.pop()\n    except KeyError:\n        pass\n",
    "import sys\nwhile sys.argv[1:] and sys.argv[1] == '-d':\n    del sys.argv[1]\n",
    "import time\nclass T:\n    error = None\nt = T()\nwhile not t.error:\n    time.sleep(1)\n",  # 다른 곳에서 바뀌는 속성
    "def shrink(h):\n    h.pop()\n\nh = [1, 2]\nwhile len(h) > 1:\n    shrink(h)\n",  # 함수에 넘겨 바꾼다
    "buf = [[None]] * 10\n",
    "def sep(path):\n    path[:0] + '/'\n    return path\n",
    "def is_png(h):\n    if h.startswith(b'PNG'):\n        return 'png'\n\nprint(is_png(b''))\n",
    "def kind(h):\n    if h[:2] == b'GI':\n        return 'gif'\n    elif h[:2] == b'PN':\n        return 'png'\n\nprint(kind(b''))\n",
    "def log(x):\n    print(x)\n\ndef run(x):\n    return log(x)\n\nrun(1)\n",
    "for ch in 'ab':\n    if ch == 'a':\n        for ch in 'cd':\n            pass\n",
    "args = ['a']\nfor a in args:\n    args = []\n    args.append(a)\n",
    "x = 0.1 + 0.2\nm = 0\nfor n in [3, 5]:\n    if n > m:\n        m = n\nprint(x == 0.3, m)\n",  # 과제에 따라 맞을 수 있는 것은 잡지 않는다
]


def test_every_logic_case_has_a_bad_example():
    covered = {case_id for _, case_id, _ in BAD}
    assert covered == {cid for cid, case in catalog.cases().items() if case["method"] == "ast"}


@pytest.mark.parametrize("code, case_id, line", BAD)
def test_bad_code_is_caught(code, case_id, line):
    compile(code, "<test>", "exec")  # 예시가 문법은 맞는지 먼저 확인한다
    (finding,) = analysis.analyze(code)
    assert (finding.case_id, finding.line) == (case_id, line)


@pytest.mark.parametrize("code", GOOD)
def test_correct_code_is_not_flagged(code):
    compile(code, "<test>", "exec")
    assert analysis.analyze(code) == []


STEP_ORDER = ["관찰", "의심 지점", "힌트", "추가 힌트"]


@pytest.mark.parametrize("code, case_id, line", BAD)
def test_hint_steps_follow_the_rules(code, case_id, line):
    (finding,) = analysis.analyze(code)
    labels = [s.label for s in finding.steps]
    assert labels == [label for label in STEP_ORDER if label in labels]
    assert "의심 지점" in labels and "힌트" in labels
    by_label = {s.label: s.text for s in finding.steps}
    assert not re.search(r"\d+번 줄", by_label.get("관찰", ""))  # 줄 번호는 '의심 지점'에서 처음 나온다
    assert f"{line}번 줄" in by_label["의심 지점"]
    for text in by_label.values():
        assert "{" not in text and "}" not in text  # 채우지 못한 자리가 없다
        assert "``" not in text
        assert not re.search(r"(?<!\d)번 줄", text)  # 빈 줄 번호가 들어간 자리도 없다


def test_hints_do_not_give_the_fix():
    steps = analysis.analyze("count = 0\nfor x in [1, 2]:\n    count = 1\n")[0].steps
    assert not any("+=" in s.text for s in steps)
    steps = analysis.analyze("x = input()\nprint(x + 1)\n")[0].steps
    assert not any("int(" in s.text for s in steps)
    steps = analysis.analyze("while True:\n    x = input()\n")[0].steps
    assert not any("break" in s.text for s in steps)
    steps = analysis.analyze("def add(a, b):\n    print(a + b)\n\nx = add(1, 2)\n")[0].steps
    assert not any("return" in s.text for s in steps)


def test_one_finding_at_a_time_and_stops_come_first():
    code = (
        "x = int(input())\n"
        "if x == 1 or 2:\n"  # 결과가 틀림 (40)
        "    print('a')\n"
        "def area(w, h):\n"
        "    return w * h\n"
        "print(area(3))\n"  # 실행하면 멈춤 (20)
    )
    (finding,) = analysis.analyze(code)
    assert finding.case_id == "func.wrong_arg_count"
    # 그것을 고치면 다음 것이 나온다.
    (finding,) = analysis.analyze(code.replace("area(3)", "area(3, 4)"))
    assert finding.case_id == "cond.or_constant"


def test_endless_loop_comes_before_wrong_results():
    code = "x = 1\nx + 1\ni = 0\nwhile i < 3:\n    print(i)\n"
    assert analysis.analyze(code)[0].case_id == "loop.while_no_update"


def test_syntax_error_hides_logic_errors():
    (finding,) = analysis.analyze("a = input()\nprint(a + 1)\nif a == 3\n    pass\n")
    assert finding.case_id == "cond.missing_colon"


@pytest.mark.allow_rule_errors
def test_a_broken_rule_does_not_break_the_analysis(monkeypatch, caplog):
    def broken(tree):
        raise RuntimeError("규칙 버그")

    monkeypatch.setattr(rules, "RULES", [broken, *rules.RULES])
    (finding,) = analysis.analyze("a = input()\nprint(a + 1)\n")
    assert finding.case_id == "var.input_not_converted"
    assert "broken" in caplog.text


def test_teacher_dashboard_names_logic_cases():
    assert analysis.case_label("loop.while_no_update") == "[반복문] while 문에서 조건을 바꾸는 문장을 빠뜨려 무한 반복하기"
    assert not analysis.is_syntax_case("loop.while_no_update")


def test_submission_with_a_logic_error(student):
    r = student.post("/api/submissions", json={"code": "i = 0\nwhile i < 5:\n    print(i)\n"})
    assert r.status_code == 201
    body = r.json()
    assert body["syntax_error"] is False  # 문법은 맞아서 실행은 된다
    (finding,) = body["findings"]
    assert finding["revealed"] == [] and finding["total_steps"] == 3
    assert "while_no_update" not in r.text and "무한" not in r.text  # 열기 전에는 무엇인지 나가지 않는다
    revealed = student.post(f"/api/findings/{finding['id']}/reveal").json()["revealed"]
    assert revealed[0]["label"] == "관찰"


# ---------- 서비스 전체 흐름 ----------


def _submit(client, code, thread_id=None):
    body = {"code": code} if thread_id is None else {"code": code, "thread_id": thread_id}
    r = client.post("/api/submissions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _reveal_all(client, finding):
    revealed = []
    for _ in range(finding["total_steps"]):
        revealed = client.post(f"/api/findings/{finding['id']}/reveal").json()["revealed"]
    return revealed


def test_fixing_one_mistake_shows_the_next_one(student):
    """한 번에 하나씩: 고쳐서 같은 스레드에 다시 내면 다음 실수가 나오고, 다 고치면 빈 목록이다."""
    code = "def area(w, h):\n    return w * h\n\nprint(area(3))\nfor i in range(3):\n    for i in range(2):\n        print(i)\n"
    first = _submit(student, code)
    assert _reveal_all(student, first["findings"][0])[1]["text"].startswith("4번 줄에서 `area`에 값을 1개")
    second = _submit(student, code.replace("area(3)", "area(3, 4)"), first["thread_id"])
    assert "6번 줄의 안쪽 `for`" in _reveal_all(student, second["findings"][0])[1]["text"]
    third = _submit(student, code.replace("area(3)", "area(3, 4)").replace("for i in range(2)", "for j in range(2)"),
                    first["thread_id"])
    assert third["findings"] == [] and third["thread_status"] == "open"  # 다 고쳤는지는 학생이 정한다


def test_dashboard_counts_logic_cases(teacher, classroom, make_student):
    for no in ("s1", "s2"):
        _submit(make_student(no), "i = 0\nwhile i < 5:\n    print(i)\n")
    errors = teacher.get(f"/api/teacher/classes/{classroom['id']}/dashboard").json()["errors"]
    assert errors == [{
        "case_id": "loop.while_no_update",
        "label": "[반복문] while 문에서 조건을 바꾸는 문장을 빠뜨려 무한 반복하기",
        "students": 2, "threads": 2,
    }]


def test_teacher_wording_applies_to_logic_cases(teacher, student):
    steps = [
        {"label": "관찰", "text": "반복이 멈추지 않아요."},
        {"label": "의심 지점", "text": "{line}번 줄 `while`의 `{name}`을 따라가 보세요."},
        {"label": "힌트", "text": "`{name}`이 언제 바뀌나요? {unknown} 은 그대로 둔다."},
    ]
    assert teacher.put("/api/teacher/hints/loop.while_no_update", json={"steps": steps}).status_code == 200
    finding = _submit(student, "n = 3\nwhile n > 0:\n    print(n)\n")["findings"][0]
    assert [s["text"] for s in _reveal_all(student, finding)] == [
        "반복이 멈추지 않아요.",
        "2번 줄 `while`의 `n`을 따라가 보세요.",
        "`n`이 언제 바뀌나요? {unknown} 은 그대로 둔다.",
    ]
    placeholders = next(c for c in teacher.get("/api/teacher/hints").json() if c["id"] == "loop.while_no_update")
    assert [p["name"] for p in placeholders["placeholders"]] == ["line", "name"]  # 편집 화면에 쓸 수 있는 자리를 알려 준다


# 알고도 놓치는 것 (미탐). 잡도록 바꾸면 이 목록에서 BAD 로 옮긴다.
KNOWN_MISSES = [
    # break 가 있으면 끝날 수 있다고 보고 넘어간다. 여기서는 i 가 0 에 머물러 break 에 닿지 못해 끝나지 않는다.
    "i = 0\nwhile i < 5:\n    if i == 3:\n        break\n    print(i)\n",
    # 이번 범위 밖: 경계값, range 끝, 이름 오타, 만들기 전 사용, 최댓값 초기값 0
    "score = int(input())\nif score > 60:\n    print('합격')\n",
    "total = 0\nfor i in range(5):\n    totla = total + i\nprint(total)\n",
    "print(result)\nresult = 3\n",
    "m = 0\nfor n in [-3, -1]:\n    if n > m:\n        m = n\n",
]


@pytest.mark.parametrize("code", KNOWN_MISSES)
def test_known_misses_are_documented(code):
    assert analysis.analyze(code) == []
