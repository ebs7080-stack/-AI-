"""로그인 실패 횟수 제한.

같은 주소(IP)에서 로그인·입장·가입에 계속 실패하면 한동안 막는다. 비밀번호나 교실 코드, 초대 코드를
마구 넣어 보는 것을 느리게 만들기 위한 것이다. 기록은 프로세스 메모리에만 둔다 (서버는 프로세스
하나로 돈다. app/hub.py 와 같은 전제).

터널이나 프록시 뒤에서는 uvicorn 이 X-Forwarded-For 를 읽어 request.client 에 실제 접속자의 주소를
넣어 준다 (기본값으로 127.0.0.1 에서 온 프록시 헤더만 믿는다).
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

WINDOW_SECONDS = 10 * 60
MAX_FAILURES = 20  # 한 교실이 같은 공유기 뒤에서 입장하다 오타를 내는 정도로는 걸리지 않게 넉넉히 둔다.

_failures: dict[str, deque[float]] = defaultdict(deque)


def _key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _recent(key: str) -> deque[float]:
    times = _failures[key]
    cutoff = time.monotonic() - WINDOW_SECONDS
    while times and times[0] < cutoff:
        times.popleft()
    if not times:
        _failures.pop(key, None)
    return times


def check(request: Request) -> None:
    """이 주소가 너무 많이 실패했으면 429 로 거절한다. 로그인을 확인하기 전에 부른다."""
    if len(_recent(_key(request))) >= MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="여러 번 실패해서 잠시 막아 두었어요. 10분쯤 뒤에 다시 시도해 주세요.",
        )


def record_failure(request: Request) -> None:
    key = _key(request)
    _recent(key)
    _failures[key].append(time.monotonic())


def reset() -> None:
    """기록을 모두 지운다 (테스트용)."""
    _failures.clear()
