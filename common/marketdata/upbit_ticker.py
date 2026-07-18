"""업비트 현재가 조회 (단일 책임: 공개 REST ticker → {심볼: 최근 체결가}).

trade_once가 마크·주문가로 쓸 코인 현재가. 틱 아카이브(ClickHouse `ticks`)에 의존하지 않고
업비트 공개 REST로 조회 → 틱 수집 전용 VM과 온디맨드 매매 VM을 디커플링(매매 VM은 틱 DB 불요).
인증 불필요. 재시도/백오프는 common.http_client.get_json 재사용.

실행(디버그): PYTHONPATH=. .venv/Scripts/python.exe -m common.marketdata.upbit_ticker
"""
from decimal import Decimal

from common.http_client import get_json

_TICKER_URL = "https://api.upbit.com/v1/ticker"


def latest_prices(symbols) -> dict:
    """심볼 목록(예: ['KRW-BTC','KRW-ETH']) → {심볼: Decimal(최근 체결가)}. 빈 목록이면 {}."""
    syms = [s for s in symbols if s]
    if not syms:
        return {}
    data = get_json(_TICKER_URL, params={"markets": ",".join(syms)})
    return {row["market"]: Decimal(str(row["trade_price"])) for row in data}


if __name__ == "__main__":
    import sys

    from common.config import ENSEMBLE_SYMBOLS
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(latest_prices(ENSEMBLE_SYMBOLS))
