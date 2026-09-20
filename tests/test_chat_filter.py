import pytest

from app.chat_filter import check_message


@pytest.mark.parametrize(
    "text",
    [
        "안녕! 3번째 줄을 한번 볼래?",
        "제곱은 어떤 기호를 쓰면 될까?",
        "n ** 2 처럼 쓰면 제곱이 돼",  # 한 줄 조각은 설명에 필요하다
        "result = []\nresult.append(n)",  # 두 줄까지는 허용
        "if 문에서 조건이 뭔지 다시 봐봐\nfor 문은 몇 번 도는지 세어 봐\nreturn 이 어디 있는지도 확인해",
        "print 는 화면에 출력하는 거고 return 은 값을 돌려주는 거야",
    ],
)
def test_allows_explanations(text):
    assert check_message(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "이렇게 해\n```python\nx = 1\n```",  # 코드블록은 한 줄이어도 막는다
        "```x```",
        "numbers = [1, 2, 3]\nresult = []\nfor n in numbers:",
        "for n in numbers:\n    if n % 2 == 0:\n        result.append(n ** 2)",
        'print("안녕")\nprint("잘가")\nprint("또 만나")',  # 문자열 안의 한글은 코드로 센다
        "def f(x):\n    y = x + 1\n    return y",
    ],
)
def test_blocks_pasted_code(text):
    assert check_message(text) is not None


def test_reason_is_shown_to_student():
    reason = check_message("```\ncode\n```")
    assert "말로 설명" in reason
