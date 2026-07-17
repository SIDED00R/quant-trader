"""FastAPI 앱 진입점 (단일 책임: 앱 조립 + 수명주기)."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api import auth_google, stock_order_executor, warmup
from api.routes import account, autotrade, decisions, equity, health, history, market, orders, performance, rebalance, stock_orders, stocks, strategy, watchlist, web
from api.security import auth_gate
from common.config import SESSION_SECRET, SITE_ADDRESS
from common.postgres_client import close_pool, open_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    warm = asyncio.create_task(warmup.warm_caches())              # 캐시 예열 — 기동 비차단
    executor = asyncio.create_task(stock_order_executor.run())    # 수동주문 실행기(상시)
    yield
    executor.cancel()
    if not warm.done():
        warm.cancel()
    close_pool()


app = FastAPI(title="quant-trader API", lifespan=lifespan)

# 인증 게이트(내부) → 세션(외부) 순으로 추가: 세션이 먼저 실행돼 request.session 을 채운 뒤 게이트가 검사.
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_gate)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=bool(SITE_ADDRESS),  # 공개(HTTPS) 배포 시 Secure 쿠키
    same_site="lax",
)
app.add_middleware(GZipMiddleware, minimum_size=500)   # 최외곽 — HTML/JSON 응답 압축

app.include_router(auth_google.router)
app.include_router(health.router)
app.include_router(web.router)
app.include_router(market.router)
app.include_router(history.router)
app.include_router(orders.router)
app.include_router(account.router)
app.include_router(autotrade.router)
app.include_router(strategy.router)
app.include_router(performance.router)
app.include_router(decisions.router)
app.include_router(equity.router)
app.include_router(stocks.router)
app.include_router(stock_orders.router)
app.include_router(rebalance.router)
app.include_router(watchlist.router)
