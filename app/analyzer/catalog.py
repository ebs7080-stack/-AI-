"""cases.toml 을 읽어 case 별 교사용 이름과 단계별 힌트를 만들어 준다."""

import re
import tomllib
from functools import cache
from pathlib import Path

from . import Match

CASES_PATH = Path(__file__).with_name("cases.toml")

# 힌트 문구에서 {이름} 으로 쓸 수 있는 자리와 그 뜻. 분석기가 찾은 값으로 채워진다.
PLACEHOLDERS = {
    "line": "오류가 난 줄 번호",
    "keyword": "그 줄의 첫 단어 (if, for, def 등)",
    "opener": "딸린 문장이 와야 하는 줄의 종류 (if, for, def 등)",
    "opener_line": "그 줄의 번호",
    "char": "문제가 된 글자",
}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@cache
def cases() -> dict[str, dict]:
    """case_id -> {id, category, label, method, steps}."""
    with CASES_PATH.open("rb") as f:
        return {case["id"]: case for case in tomllib.load(f)["case"]}


def labels() -> dict[str, str]:
    """case_id -> 교사 대시보드에 보여 줄 이름."""
    return {case_id: f"[{case['category']}] {case['label']}" for case_id, case in cases().items()}


def placeholders_in(steps: list[dict]) -> list[str]:
    """그 문구들이 쓰는 자리 이름. 그 case 에서 분석기가 채워 줄 수 있는 값이 무엇인지 알려 준다."""
    used = {name for step in steps for name in _PLACEHOLDER.findall(step["text"])}
    return [name for name in PLACEHOLDERS if name in used]


def fill(text: str, values: dict[str, object]) -> str:
    """문구의 {line} 같은 자리를 찾은 값으로 채운다.

    교사가 직접 쓴 문구도 여기를 지나므로 str.format 은 쓰지 않는다 (중괄호를 잘못 써도 멈추지 않고,
    값의 속성을 꺼내는 표현도 통하지 않는다). 모르는 이름은 쓴 그대로 두고, 아는 이름인데 이번에
    찾은 값이 없으면 비워 둔다.
    """

    def replace(found: re.Match) -> str:
        name = found.group(1)
        if name not in PLACEHOLDERS:
            return found.group(0)
        value = values.get(name)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(replace, text)


def values_of(match: Match) -> dict[str, object]:
    return {**match.values, "line": match.line if match.line is not None else "?"}


def fill_steps(steps: list[dict], values: dict[str, object]) -> list[tuple[str, str]]:
    """단계 문구 틀을 (단계 이름, 채운 문구) 목록으로."""
    return [(step["label"], fill(step["text"], values)) for step in steps]
