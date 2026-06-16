"""업비트 마켓 목록 조회 (단일 책임: REST 마켓 메타)."""
import httpx

MARKET_ALL_URL = "https://api.upbit.com/v1/market/all"


def fetch_krw_markets() -> list[str]:
    """업비트 KRW 마켓 코드 목록(예: ['KRW-BTC', 'KRW-ETH', ...])."""
    resp = httpx.get(MARKET_ALL_URL, params={"isDetails": "false"}, timeout=10.0)
    resp.raise_for_status()
    return [m["market"] for m in resp.json() if m["market"].startswith("KRW-")]
