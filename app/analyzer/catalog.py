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
    "other_line": "함께 볼 다른 줄 번호 (함수 정의, 반복문 시작 등)",
    "name": "문제가 된 변수·함수 이름",
    "name2": "함께 볼 다른 이름",
    "expr": "학생이 쓴 식",
    "count": "실제로 쓴 개수",
    "expected": "필요한 개수",
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
        if value is None:
            return ""
        value = str(value)
        particle = found.group("particle")
        if particle is None:
            return value + found.group("quote")
        return value + found.group("quote") + _particle_for(value, particle)

    return _PLACEHOLDER_WITH_PARTICLE.sub(replace, text)


# 자리 바로 뒤(닫는 ` 다음)의 조사. 채운 값의 끝소리에 맞춰 바꾼다 ('total'을, 'count'를).
# 뒤에 한글이 이어지면(이나, 이고 ...) 조사가 아닐 수 있어 건드리지 않는다.
_PAIRS = {"이라는": "라는", "이에요": "예요", "으로": "로", "을": "를", "은": "는", "이": "가", "과": "와"}
_ALL_PARTICLES = sorted({*_PAIRS, *_PAIRS.values()}, key=len, reverse=True)
_PLACEHOLDER_WITH_PARTICLE = re.compile(
    r"\{(\w+)\}(?P<quote>`?)(?:(?P<particle>" + "|".join(_ALL_PARTICLES) + r")(?![가-힣]))?"
)


def _final_sound(word: str) -> str | None:
    """마지막 글자를 우리말로 읽을 때의 받침: 'ㄹ', 'other'(그 밖의 받침), None(받침 없음).

    영어 이름은 끝 글자로 어림한다 (total 토탈 → ㄹ, sum 섬 → 받침, count 카운트 → 없음)."""
    word = word.rstrip("`'\")]} ")
    if not word:
        return None
    ch = word[-1]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - ord("가")) % 28
        return None if jong == 0 else ("ㄹ" if jong == 8 else "other")
    if ch.isdigit():  # 영 일 이 삼 사 오 육 칠 팔 구
        return {"0": "other", "1": "ㄹ", "3": "other", "6": "other", "7": "ㄹ", "8": "ㄹ"}.get(ch)
    low = word.lower()
    if low.endswith("e") and len(low) > 2 and low[-2] in "lmn":  # 소리 나지 않는 e: file 파일, name 네임, line 라인
        low = low[:-1]
    if low.endswith("l"):
        return "ㄹ"
    if low.endswith(("m", "n", "ng", "k", "p", "b")) or (low.endswith("g") and len(low) > 1 and low[-2] in "aeiou"):
        return "other"
    return None


def _particle_for(value: str, written: str) -> str:
    with_final = next((a for a, b in _PAIRS.items() if written in (a, b)), written)
    without_final = _PAIRS.get(with_final, written)
    sound = _final_sound(value)
    if with_final == "으로":  # ㄹ 받침 뒤에는 '로' (교실로, total로)
        return "으로" if sound == "other" else "로"
    return with_final if sound else without_final


def values_of(match: Match) -> dict[str, object]:
    return {**match.values, "line": match.line if match.line is not None else "?"}


def fill_steps(steps: list[dict], values: dict[str, object]) -> list[tuple[str, str]]:
    """단계 문구 틀을 (단계 이름, 채운 문구) 목록으로."""
    return [(step["label"], fill(step["text"], values)) for step in steps]
