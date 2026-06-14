"""FastAPI 앱 진입점 (단일 책임: 앱 조립 + 수명주기)."""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from api.routes import account, history, market, orders, web
from api.security import require_auth
from common.postgres_client import close_pool, open_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    yield
    close_pool()


app = FastAPI(
    title="coin-auto-trader API",
    lifespan=lifespan,
    dependencies=[Depends(require_auth)],
)
app.include_router(web.router)
app.include_router(market.router)
app.include_router(history.router)
app.include_router(orders.router)
app.include_router(account.router)
