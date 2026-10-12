"""학생 코드 분석기.

웹 계층은 `analyze()` 의 반환값(Finding 목록)만 사용한다. 분석 규칙과 힌트 문구는 app/analyzer/
안에 있고, 규칙을 늘려도 API/DB/화면은 그대로 둘 수 있다.
"""

from dataclasses import dataclass, field, replace

from .analyzer import catalog, syntax


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

    지금은 1단계인 문법 검사만 한다. 문법 오류가 있으면 그 case 하나만 돌려주고, 없으면 빈 목록이다.
    빈 목록은 '찾은 문제가 없다'는 뜻이지 코드가 맞다는 보장이 아니다 (이름·논리 오류는 아직 보지 않는다).
    """
    match = syntax.classify(code)
    if match is None:
        return []
    values = catalog.values_of(match)
    default = catalog.cases()[match.case_id]["steps"]
    steps = [HintStep(label, text) for label, text in catalog.fill_steps(default, values)]
    return [Finding(match.case_id, match.line, steps, values)]


def with_custom_steps(finding: Finding, templates: list[dict] | None) -> Finding:
    """교사가 그 case 의 문구를 고쳤다면 기본 힌트 대신 그 문구로 단계를 만든다."""
    if templates is None:
        return finding
    steps = [HintStep(label, text) for label, text in catalog.fill_steps(templates, finding.values)]
    return replace(finding, steps=steps)
