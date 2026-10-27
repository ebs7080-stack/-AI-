"""도움 채팅방의 실시간 연결 목록.

프로세스 메모리에만 있으므로 uvicorn 을 워커 하나로 실행하는 것을 전제로 한다
(start.bat 이 그렇게 실행한다). 워커를 여러 개로 늘리려면 Redis 같은 공용 채널이 필요하다.
"""

import asyncio
from collections import defaultdict

import anyio.from_thread
from fastapi import WebSocket

SEND_TIMEOUT = 3.0


class Hub:
    def __init__(self) -> None:
        # 소켓마다 그 소켓이 돌고 있는 이벤트 루프를 함께 기억한다.
        self._rooms: dict[int, dict[WebSocket, asyncio.AbstractEventLoop]] = defaultdict(dict)

    def join(self, session_id: int, ws: WebSocket) -> None:
        self._rooms[session_id][ws] = asyncio.get_running_loop()

    def leave(self, session_id: int, ws: WebSocket) -> None:
        room = self._rooms.get(session_id)
        if room is not None:
            room.pop(ws, None)
            if not room:
                del self._rooms[session_id]

    async def broadcast(self, session_id: int, event: dict) -> None:
        current = asyncio.get_running_loop()
        for ws, loop in list(self._rooms.get(session_id, {}).items()):
            try:
                if loop is current:  # 운영 환경에서는 언제나 이쪽이다.
                    await ws.send_json(event)
                else:  # 다른 루프의 소켓은 그 루프에서 보내야 안전하다 (테스트 클라이언트가 그렇다).
                    future = asyncio.run_coroutine_threadsafe(ws.send_json(event), loop)
                    await asyncio.wait_for(asyncio.wrap_future(future), SEND_TIMEOUT)
            except Exception:  # 이미 끊어진 연결은 목록에서 치운다.
                self.leave(session_id, ws)


hub = Hub()


def publish(session_id: int, event: dict) -> None:
    """동기 엔드포인트(스레드 풀에서 실행됨)에서 채팅방 참가자에게 이벤트를 보낸다."""
    try:
        anyio.from_thread.run(hub.broadcast, session_id, event)
    except RuntimeError:  # 워커 스레드 밖에서 불렸다면 보낼 이벤트 루프가 없다.
        pass
