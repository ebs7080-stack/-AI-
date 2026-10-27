import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .collab import router as collab_router
from .db import init_db
from .teacher import router as teacher_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# 문법 오류 분류(app/analyzer/syntax.py)는 이 버전의 오류 메시지 문구로 맞춰 두었다.
SUPPORTED_PYTHON = (3, 12)


class NoCacheStaticFiles(StaticFiles):
    """화면 파일은 이름이 바뀌지 않는 빌드 없는 프론트엔드라서, 브라우저가 매번 서버에 물어보게 한다.

    캐시 헤더가 없으면 브라우저가 수정 시각만 보고 옛 JS 를 한동안 그대로 쓰기 때문에, 새 HTML 과
    옛 JS 가 섞여 버튼이 아무 반응도 하지 않을 수 있다. 바뀌지 않았으면 서버는 304 만 돌려준다.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    if sys.version_info[:2] != SUPPORTED_PYTHON:
        # 서버는 그대로 켜되, 문법 오류가 엉뚱한 case 로 분류될 수 있음을 알린다.
        logging.getLogger("uvicorn.error").warning(
            "파이썬 %d.%d 에서 실행 중입니다. 문법 오류 분류는 %d.%d 에 맞춰져 있으니 "
            "tests/test_syntax.py 를 먼저 돌려 보세요.",
            *sys.version_info[:2], *SUPPORTED_PYTHON,
        )
    init_db()
    yield


app = FastAPI(title="코딩입문 중고등학생을 위한 단계적 피드백 웹서비스", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)
app.include_router(collab_router)
app.include_router(teacher_router)

# API 라우트보다 뒤에 마운트해야 /api/* 가 정적 파일에 가려지지 않는다.
app.mount("/", NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="static")
