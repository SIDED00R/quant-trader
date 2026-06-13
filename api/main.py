"""FastAPI 앱 진입점 (단일 책임: 앱 조립 + 수명주기)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes import account, orders
from common.kafka_client import create_producer
from common.postgres_client import close_pool, open_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    app.state.producer = create_producer()
    yield
    app.state.producer.flush(5)
    close_pool()


app = FastAPI(title="coin-auto-trader API", lifespan=lifespan)
app.include_router(orders.router)
app.include_router(account.router)
