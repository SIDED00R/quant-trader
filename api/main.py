"""FastAPI 앱 진입점 (단일 책임: 앱 조립 + 수명주기)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api import auth_google
from api.routes import account, autotrade, history, market, orders, performance, strategy, web
from api.security import auth_gate
from common.config import SESSION_SECRET, SITE_ADDRESS
from common.postgres_client import close_pool, open_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    yield
    close_pool()


app = FastAPI(title="coin-auto-trader API", lifespan=lifespan)

# 인증 게이트(내부) → 세션(외부) 순으로 추가: 세션이 먼저 실행돼 request.session 을 채운 뒤 게이트가 검사.
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_gate)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=bool(SITE_ADDRESS),  # 공개(HTTPS) 배포 시 Secure 쿠키
    same_site="lax",
)

app.include_router(auth_google.router)
app.include_router(web.router)
app.include_router(market.router)
app.include_router(history.router)
app.include_router(orders.router)
app.include_router(account.router)
app.include_router(autotrade.router)
app.include_router(strategy.router)
app.include_router(performance.router)
