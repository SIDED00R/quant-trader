"""시세 조회 라우트 (단일 책임: ClickHouse 시세/캔들 조회)."""
from fastapi import APIRouter, HTTPException

from common.clickhouse_client import create_client
from common.symbols import resolve_symbols

router = APIRouter(prefix="/market")

_client = None


def _ch():
    global _client
    if _client is None:
        _client = create_client()
    return _client


@router.get("/symbols")
def symbols():
    return resolve_symbols()


@router.get("/prices")
def prices():
    """종목별 최신 체결가 (최근 1시간 내 틱의 seq 최대값)."""
    res = _ch().query(
        "SELECT symbol, argMax(price, seq) AS price "
        "FROM ticks WHERE trade_ts > now() - INTERVAL 1 HOUR GROUP BY symbol"
    )
    return {row[0]: float(row[1]) for row in res.result_rows}


@router.get("/candles/{symbol}")
def candles(symbol: str):
    """최근 2시간 1분봉 (차트용)."""
    if symbol not in resolve_symbols():
        raise HTTPException(404, "unknown symbol")
    res = _ch().query(
        "SELECT window_start, open, high, low, close, volume "
        "FROM candles_1m FINAL "
        "WHERE symbol = {symbol:String} AND window_start > now() - INTERVAL 2 HOUR "
        "ORDER BY window_start",
        parameters={"symbol": symbol},
    )
    return [
        {
            "t": row[0].isoformat(),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in res.result_rows
    ]
