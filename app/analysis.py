"""학생 코드 분석기.

웹 계층은 `analyze()` 의 반환값(Finding 목록)만 사용한다. 분석 규칙과 힌트 문구는 app/analyzer/
안에 있고, 규칙을 늘려도 API/DB/화면은 그대로 둘 수 있다.
"""

import ast
from dataclasses import dataclass, field, replace

from .analyzer import Match, catalog, rules, syntax

# 한 번에 보여 줄 의심 지점 수. 초보자가 무엇부터 고칠지 헷갈리지 않게 하나만 보여 주고,
# 고쳐서 다시 내면 다음 것이 나온다 (2026-10-27 사용자 결정).
MAX_FINDINGS = 1


@dataclass(frozen=True)
class HintStep:
    label: str  # 관찰 / 의심 지점 / 힌트 / 추가 힌트
    text: str


@dataclass(frozen=True)
class Finding:
    case_id: str  # 오류case 의 식별자. 학생에게는 노출하지 않는다.
    line: int | None
    steps: list[HintStep] = field(default_factory=list)
    # 힌트 문구의 {line} 같은 자리에 채울 값. 교사가 고친 문구로 단계를 다시 만들 때 쓴다.
    values: dict[str, object] = field(default_factory=dict)


# 분석기를 붙이기 전의 임시 분석기가 남긴 기록. 새 제출에는 생기지 않는다.
LEGACY_LABELS = {"stub": "임시 분석 결과 (분석기 연결 전의 기록)"}


def case_label(case_id: str) -> str:
    """case_id -> 교사 통계에 보여 줄 이름. 목록에 없는 id 는 id 를 그대로 보여 준다."""
    return catalog.labels().get(case_id) or LEGACY_LABELS.get(case_id, case_id)


def is_syntax_case(case_id: str) -> bool:
    """문법 오류 case 인지. 문법 오류가 있는 코드는 한 줄도 실행되지 않는다."""
    case = catalog.cases().get(case_id)
    return case is not None and case["method"] == "syntax"


def analyze(code: str) -> list[Finding]:
    """코드의 의심 지점을 찾는다. 코드를 실행하지 않는다.

    1) 문법 검사: 문법 오류가 있으면 그 case 하나만 돌려주고 끝낸다 (논리를 따질 수 없다).
    2) 논리 규칙(rules/): 찾은 것 중 우선순위가 가장 높은 MAX_FINDINGS 개만 돌려준다.
    빈 목록은 '찾은 문제가 없다'는 뜻이지 코드가 맞다는 보장이 아니다 (규칙은 오탐보다 미탐을 택한다).
    """
    match = syntax.classify(code)
    if match is not None:
        return [_finding(match)]
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []  # 문법 검사가 읽지 못한 코드 (너무 깊은 중첩 등)
    return [_finding(m) for m in _pick(rules.run(tree))]


def _pick(matches: list[Match]) -> list[Match]:
    """같은 case·줄은 하나로 합치고, 우선순위(작을수록 먼저)와 줄 순서로 골라낸다."""
    cases = catalog.cases()
    unique = {(m.case_id, m.line): m for m in matches if m.case_id in cases}
    ordered = sorted(unique.values(), key=lambda m: (cases[m.case_id].get("priority", 50), m.line or 0))
    return ordered[:MAX_FINDINGS]


def _finding(match: Match) -> Finding:
    values = catalog.values_of(match)
    default = catalog.cases()[match.case_id]["steps"]
    steps = [HintStep(label, text) for label, text in catalog.fill_steps(default, values)]
    return Finding(match.case_id, match.line, steps, values)


def with_custom_steps(finding: Finding, templates: list[dict] | None) -> Finding:
    """교사가 그 case 의 문구를 고쳤다면 기본 힌트 대신 그 문구로 단계를 만든다."""
    if templates is None:
        return finding
    steps = [HintStep(label, text) for label, text in catalog.fill_steps(templates, finding.values)]
    return replace(finding, steps=steps)
