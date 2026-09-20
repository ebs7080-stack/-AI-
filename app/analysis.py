"""학생 코드 분석기.

웹 계층은 `analyze()` 의 반환값(Finding 목록)만 사용한다. 실제 정적 분석기를 만들면
이 함수의 내부만 교체하면 되고 API/DB/화면은 그대로 둘 수 있다.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HintStep:
    label: str  # 관찰 / 의심 지점 / 힌트 / 추가 힌트
    text: str


@dataclass(frozen=True)
class Finding:
    case_id: str  # 오류case 의 식별자. 학생에게는 노출하지 않는다.
    line: int | None
    steps: list[HintStep] = field(default_factory=list)


# case_id -> 교사 통계에 보여 줄 이름. 오류case 를 id 가 붙은 목록으로 바꾸면 여기서 읽어 온다.
CASE_LABELS = {"stub": "임시 분석 결과 (분석기 연결 전)"}


def case_label(case_id: str) -> str:
    return CASE_LABELS.get(case_id, case_id)


def analyze(code: str) -> list[Finding]:
    """임시 분석기: 실제 오류를 찾지 않고 화면 흐름 확인용 더미 결과를 돌려준다."""
    line_count = len(code.splitlines())
    return [
        Finding(
            case_id="stub",
            line=None,
            steps=[
                HintStep("관찰", f"제출한 코드는 {line_count}줄입니다."),
                HintStep("의심 지점", "아직 실제 분석기가 연결되지 않아 오류를 찾지 않았습니다. (임시 결과)"),
                HintStep("힌트", "코드를 한 줄씩 따라가며 변수의 값이 어떻게 바뀌는지 종이에 적어 보세요."),
            ],
        )
    ]
