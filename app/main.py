from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .collab import router as collab_router
from .db import init_db
from .teacher import router as teacher_router
from .teacher_portal import router as teacher_portal_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


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
    init_db()
    yield


app = FastAPI(title="Code Web Analyzer", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)
app.include_router(collab_router)
app.include_router(teacher_router)
app.include_router(teacher_portal_router)

# API 라우트보다 뒤에 마운트해야 /api/* 가 정적 파일에 가려지지 않는다.
app.mount("/", NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="static")
