"""채팅 정답 차단 규칙 (FR-2.3).

requirements.md 6번의 열린 이슈라서 지금은 임시 규칙이다. 판정은 `check_message()` 하나로만
드러나므로, 더 나은 규칙(AST 비교, 유사도 등)으로 바꿀 때 이 파일만 고치면 된다.

- ``` 코드블록이 있으면 막는다.
- 파이썬 문장처럼 보이는 줄이 MAX_CODE_LINES 를 넘으면 막는다.
  `n ** 2` 같은 한 줄짜리 조각은 설명에 꼭 필요하므로 허용한다.

변수 이름만 바꿔 보내거나 이미지로 우회하는 경우는 잡지 못한다 (NFR-1).
"""

import re

MAX_CODE_LINES = 2

BLOCK_REASON = (
    "정답 코드를 그대로 보내면 친구가 스스로 고칠 기회가 없어져요. "
    "어디를 왜 고쳐야 하는지 말로 설명해 주세요."
)

_STRING = re.compile(r"\"[^\"]*\"|'[^']*'")
_HANGUL = re.compile(r"[가-힣]")
_STATEMENT = re.compile(
    r"^(?:"
    r"(?:def|class|for|while|if|elif|else|try|except|finally|with)\b.*:"  # 블록을 여는 줄
    r"|(?:return|import|from|break|continue|pass|raise|assert|del|print)\b.*"
    r"|[A-Za-z_][\w.\[\]]*\s*(?:[-+*/%]|//|\*\*)?=(?!=).+"  # 대입
    r"|[A-Za-z_][\w.]*\(.*\)"  # 함수 호출
    r")\s*$"
)


def _looks_like_code(line: str) -> bool:
    # 문자열과 주석 안의 한글은 코드일 수 있으므로 지우고 나서 한글이 남는지 본다.
    # 한글이 남으면 "if 문에서 ..." 같은 설명 문장이다.
    bare = _STRING.sub('""', line).split("#", 1)[0]
    if _HANGUL.search(bare):
        return False
    return bool(_STATEMENT.match(line.strip()))


def check_message(text: str) -> str | None:
    """막아야 하면 학생에게 보여 줄 이유를, 보내도 되면 None 을 돌려준다."""
    if "```" in text:
        return BLOCK_REASON
    code_lines = sum(1 for line in text.splitlines() if line.strip() and _looks_like_code(line))
    if code_lines > MAX_CODE_LINES:
        return BLOCK_REASON
    return None
