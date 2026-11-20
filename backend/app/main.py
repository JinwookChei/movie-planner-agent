"""FastAPI 생성, CORS, 라우터 등록, 시작 시 DB 초기화와 로깅 Agent 구독. (M8, F3017, NF3006)

실행 (backend/ 폴더에서)
  ..\\.venv\\Scripts\\python -m uvicorn app.main:app --reload --port 8000
  → http://127.0.0.1:8000/docs  (Swagger: 요청을 직접 보내 볼 수 있다)

로깅 Agent를 버스에 연결하는 곳은 여기 한 곳뿐이다 (M7).
  메인 Agent 코드(app/agent/)는 logger_agent를 모른다. 아래 start() 한 줄을 지워도 대화는 정상이다 (체크리스트 15).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config, db
from app.api import chat, health, logs, plans
from app.events import logger_agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()            # 테이블이 없으면 만든다 (여러 번 해도 안전)
    logger_agent.start()    # 버스 구독 시작
    yield                   # ── 여기서 서버가 요청을 받는다 ──
    logger_agent.stop()


app = FastAPI(
    title="MoviePlanner Agent API",
    version="1.0.0",
    description="시간 조건에 맞는 영화 관람 계획을 제안하는 Agent. 경로는 대화(chat) · 계획(plans) · 로그(logs) · 상태(health)로 나뉜다.",
    lifespan=lifespan,
)

# 프론트(5500 포트)는 백엔드(8000 포트)와 출처가 달라서, 허용하지 않으면 브라우저가 응답을 막는다
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS,
                   allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])

for r in (chat.router, plans.router, logs.router, health.router):
    app.include_router(r)
