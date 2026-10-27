"""문법 오류를 case 로 나눈다 (docs/static-analysis.md 1단계, ast 정적분석 가이드 4장).

코드를 실행하지 않고 ast.parse 로 읽기만 한다. 실패하면 SyntaxError 의 메시지와 오류가 난 줄의
원문으로 case 를 하나 정한다. 메시지 문구는 파이썬 버전마다 조금씩 바뀌므로 전체가 아니라 핵심
부분 문자열로 견주고, tests/test_syntax.py 의 예시로 버전을 올릴 때 확인한다 (Python 3.12 기준).
"""

import ast
import keyword
import re

from . import Match

UNKNOWN = "syntax.unknown"

# "expected an indented block after 'if' statement on line 1" / "... after function definition on line 1"
_BLOCK_OPENER = re.compile(r"expected an indented block after (?:'(\w+)' statement|(function definition)) on line (\d+)")
_INVALID_CHAR = re.compile(r"invalid (?:non-printable )?character '(.+?)' \(U\+")
_NEVER_CLOSED = re.compile(r"'(.)' was never closed")
# `1st = 3`: = 왼쪽이 숫자로 시작하는 이름
_DIGIT_NAME = re.compile(r"^\d\w*\s*=(?!=)")
# `my name = 3`: = 왼쪽이 빈칸으로 나뉜 여러 단어
_SPACED_NAME = re.compile(r"^([^\W\d]\w*)(?:\s+\w+)+\s*=(?!=)")


def classify(code: str) -> Match | None:
    """문법 오류가 있으면 그 case 하나를, 없으면 None 을 돌려준다.

    파이썬은 첫 번째 문법 오류만 알려 주므로 결과도 언제나 하나다. 학생이 고쳐서 다시 제출하면
    다음 오류가 나온다. 어느 case 인지 확실하지 않으면 틀린 안내를 하지 않도록 syntax.unknown 으로 둔다.
    """
    try:
        ast.parse(code)
    except SyntaxError as error:  # IndentationError, TabError 도 SyntaxError 의 한 종류다.
        return _classify_error(error, code)
    except (ValueError, RecursionError, MemoryError):
        # 문법 오류는 아니지만 읽지 못한 코드(너무 깊은 중첩 등). 틀렸다고 단정하지 않는다.
        return None
    return None


def _classify_error(error: SyntaxError, code: str) -> Match:
    msg = error.msg or ""
    text = (error.text or "").strip()  # 오류가 난 줄의 원문
    first = text.split(maxsplit=1)[0].rstrip(":") if text else ""  # 그 줄의 첫 단어
    line = error.lineno if error.lineno is not None else max(1, len(code.splitlines()))

    def match(case_id: str, **values: object) -> Match:
        return Match(case_id, line, {"keyword": first, **values})

    # 위에서부터 차례로 검사해 처음 맞는 규칙을 쓴다. (else if 는 if 보다 먼저 봐야 한다.)
    if "expected ':'" in msg:
        if re.match(r"else\s+if\b", text):
            return match("cond.else_if")
        if first in ("if", "elif", "else"):
            return match("cond.missing_colon")
        if first in ("for", "while"):
            return match("loop.missing_colon")
        if first == "def":
            return match("func.missing_colon")

    if "expected '('" in msg and first == "def":
        return match("func.def_missing_paren")

    # 같은 메시지가 함수 호출의 인자 같은 곳에서도 나오므로 조건을 쓰는 줄일 때만 고른다.
    if "Maybe you meant '=='" in msg and first in ("if", "elif", "while"):
        return match("cond.assign_in_if")

    opener = _BLOCK_OPENER.search(msg)
    if opener:
        word = opener.group(1) or "def"
        values = {"opener": word, "opener_line": int(opener.group(3))}
        if word in ("if", "elif", "else"):
            return match("cond.body_not_indented", **values)
        if word in ("for", "while"):
            # 본문이 와야 할 자리에 또 반복문이 같은 들여쓰기로 있으면 안쪽 반복문을 잘못 둔 것이다.
            if first in ("for", "while"):
                return match("nested.inner_same_indent", **values)
            return match("loop.body_not_indented", **values)
        if word == "def":
            return match("func.body_not_indented", **values)

    if "unindent does not match" in msg:
        if first in ("else", "elif"):
            return match("cond.else_misaligned")
        return match("syntax.indent_mismatch")
    if "unexpected indent" in msg:
        return match("syntax.unexpected_indent")

    if "invalid decimal literal" in msg and _DIGIT_NAME.match(text):
        return match("var.name_starts_with_digit")
    if msg == "invalid syntax":
        spaced = _SPACED_NAME.match(text)
        # `return x = 3` 처럼 예약어로 시작하는 줄은 변수 이름의 문제가 아니다.
        if spaced and not keyword.iskeyword(spaced.group(1)):
            return match("var.name_with_space")

    never_closed = _NEVER_CLOSED.search(msg)
    if never_closed:
        return match("syntax.unclosed_bracket", char=never_closed.group(1))
    if "unterminated" in msg and "string literal" in msg:
        return match("syntax.unclosed_string")
    invalid_char = _INVALID_CHAR.search(msg)
    if invalid_char:
        return match("syntax.fullwidth_char", char=invalid_char.group(1))

    return match(UNKNOWN)
