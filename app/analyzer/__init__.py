"""학생 코드 분석기의 안쪽. 웹 계층은 app/analysis.py 의 analyze() 만 쓴다.

- cases.toml: 오류 case 목록과 단계별 힌트 문구
- catalog.py: cases.toml 을 읽어 교사용 이름과 힌트 단계를 만들어 준다
- syntax.py: 문법 오류(SyntaxError)를 case 로 나눈다
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Match:
    """분석기가 찾은 의심 지점 하나. values 는 힌트 문구의 {이름} 자리에 채울 값이다."""

    case_id: str
    line: int | None
    values: dict[str, object] = field(default_factory=dict)
