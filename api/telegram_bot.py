"""텔레그램 /차트 봇 (단일 책임: Bot API long-poll → 봉차트 응답). 상시 서비스(수집 VM·app 이미지).

인바운드는 Telegram Bot API getUpdates long-poll(httpx) — 발신용 MTProto 유저세션(notify_telegram)과
분리해 세션 충돌을 피한다. 흐름: `/차트 <종목명|티커>` → 종목 해석(common.stock_names) → 일봉 온디맨드
fetch(common.toss_daily — 수집 VM 로컬 CH엔 주식 데이터 없음) → 렌더(common.symbol_chart, KR=주봉+일목
구름 / US=일봉) → Bot API sendPhoto. TELEGRAM_ALLOWED_CHAT_IDS 화이트리스트(비면 전면 거부). 절대 크래시
안 함(예외는 백오프 후 계속). 종목명 인덱스는 /app/.namecache/names.json(주간 갱신, 실패 시 stale 유지) —
정확 티커는 사전 무관하게 항상 동작.

실행: python -m api.telegram_bot  (토큰 미설정이면 유휴 — restart 루프 방지)
"""
import io
import json
import os
import sys
import time
import traceback

import httpx

from common import rate_limit, stock_names
from common.config import TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
from common.symbol_chart import chart_for_symbol
from common.toss_daily import fetch_daily

_API = "https://api.telegram.org/bot{token}/{method}"
_CACHE = os.getenv("NAMECACHE_PATH", "/app/.namecache/names.json")
_CACHE_TTL = 7 * 24 * 3600
_KR_FETCH_DAYS, _US_FETCH_DAYS = 1000, 220
_HELP = ("📈 사용법: /차트 <종목명 또는 티커>\n"
         "예) /차트 삼성전자 · /차트 005930 · /차트 AAPL\n"
         "국장=주봉+일목 구름 · 미장=일봉")


def parse_command(text: str):
    """텍스트 → ('chart', 질의) | ('help', '') | None. `/차트`·`/chart`(@봇명 허용)·`/help`·`/start`."""
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("차트", "chart"):
        return ("chart", arg) if arg else ("help", "")
    if cmd in ("help", "start", "도움말"):
        return ("help", "")
    return None


def _url(method: str, token: str) -> str:
    return _API.format(token=token, method=method)


def send_message(token: str, chat_id, text: str) -> None:
    rate_limit.acquire("telegram", "bot")
    try:
        httpx.post(_url("sendMessage", token), data={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        print(f"[chart-bot] sendMessage 실패: {type(e).__name__}: {e}")


def send_photo(token: str, chat_id, png: bytes, caption: str) -> None:
    rate_limit.acquire("telegram", "bot")
    try:
        httpx.post(_url("sendPhoto", token), data={"chat_id": chat_id, "caption": caption[:1024]},
                   files={"photo": ("chart.png", io.BytesIO(png), "image/png")}, timeout=30)
    except Exception as e:
        print(f"[chart-bot] sendPhoto 실패: {type(e).__name__}: {e}")


def load_index() -> dict:
    """종목명 인덱스 — 신선한 파일 캐시가 있으면 사용, 아니면 재fetch 후 저장(실패 시 빈 인덱스)."""
    if os.path.exists(_CACHE) and time.time() - os.path.getmtime(_CACHE) < _CACHE_TTL:
        try:
            with open(_CACHE, encoding="utf-8") as f:
                return stock_names.build_index(json.load(f))
        except Exception:
            pass
    names = stock_names.fetch_all()
    if names.get("KR") or names.get("US"):
        try:
            os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
            with open(_CACHE, "w", encoding="utf-8") as f:
                json.dump(names, f, ensure_ascii=False)
        except Exception as e:
            print(f"[chart-bot] 이름캐시 저장 실패(비치명): {e}")
    return stock_names.build_index(names)


def handle_chart(query: str, index: dict):
    """질의 처리 → (png, caption) 성공 또는 한글 에러 문자열. 순수-ish(전송은 호출부)."""
    hit = stock_names.resolve(index, query)
    if hit is None:
        return f"❓ 종목을 찾지 못했어요: {query}\n한글명(삼성전자) 또는 티커(005930, AAPL)로 보내주세요"
    market, symbol, name = hit
    days = _KR_FETCH_DAYS if market == "KR" else _US_FETCH_DAYS
    try:
        rows = fetch_daily(symbol, days, log=lambda *a: None)
    except Exception as e:
        return f"⚠️ {symbol} 시세 조회 실패: {type(e).__name__}"
    if not rows:
        return f"⚠️ {symbol} 시세를 가져오지 못했어요(상장폐지·티커 오류일 수 있어요)"
    daily = [(r[1].date(), r[2], r[3], r[4], r[5]) for r in rows]
    try:
        return chart_for_symbol(daily, market, symbol, name if name != symbol else None)
    except ValueError as e:
        return f"⚠️ {symbol} 차트 생성 실패: {e}"


def _handle_update(token: str, upd: dict, index: dict) -> None:
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return
    if TELEGRAM_ALLOWED_CHAT_IDS and chat_id not in TELEGRAM_ALLOWED_CHAT_IDS:
        print(f"[chart-bot] 비허용 chat_id={chat_id} drop")
        return
    parsed = parse_command(msg.get("text", ""))
    if parsed is None:
        return
    if parsed[0] == "help":
        send_message(token, chat_id, _HELP)
        return
    res = handle_chart(parsed[1], index)
    if isinstance(res, tuple):
        send_photo(token, chat_id, res[0], res[1])
    else:
        send_message(token, chat_id, res)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    token = TELEGRAM_BOT_TOKEN
    if not token:
        print("[chart-bot] TELEGRAM_BOT_TOKEN 미설정 — 유휴(재시작 루프 방지). 토큰 주입 후 컨테이너 재시작 필요.")
        while True:                       # exit 0 시 restart:unless-stopped 루프 방지 → 유휴 대기
            time.sleep(3600)
    index = load_index()
    idx_at = time.time()
    offset, backoff = 0, 1
    print(f"[chart-bot] 시작 — 허용 chat {len(TELEGRAM_ALLOWED_CHAT_IDS)}개, 종목명 {len(index.get('rows', []))}개")
    with httpx.Client(timeout=70) as client:
        while True:
            try:
                r = client.get(_url("getUpdates", token), params={"offset": offset, "timeout": 50})
                r.raise_for_status()
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        _handle_update(token, upd, index)
                    except Exception:
                        traceback.print_exc()
                if time.time() - idx_at > _CACHE_TTL:       # 주간 인덱스 갱신
                    index, idx_at = load_index(), time.time()
                backoff = 1
            except Exception as e:                           # 409(다중 소비자)·네트워크 등 → 백오프 후 계속(크래시 금지)
                print(f"[chart-bot] poll 오류(재시도): {type(e).__name__}: {e}")
                time.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    raise SystemExit(main())
