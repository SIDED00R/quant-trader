"""환경 설정 로딩 (단일 책임: 설정 — env 스칼라는 Settings로 타입·검증, 파생값·상수는 모듈 레벨).

소비자는 기존대로 `from common.config import X`. 순수 env 스칼라는 Settings 필드로 두고
모듈 `__getattr__`(PEP 562)이 `_settings`에 위임한다. CSV/분기/합성 파생값과 비-env 상수는
모듈 전역으로 정의해 이름 그대로 노출한다(전역이 __getattr__보다 우선).

프로세스 env(컨테이너=docker `environment`, 로컬=`.env`)가 최우선. 잘못된 타입 값(예: 빈 문자열→int)은
import 시점 ValidationError로 조기 실패한다(기존의 조용한 폴백 대비 강화 — CI import 스윕이 머지 전 차단).
"""
from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """env 스칼라(타입·기본값·검증). CSV·집합·분기 파생값은 모듈 레벨에서 구성한다(아래)."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    KAFKA_BOOTSTRAP_SERVERS: str = "127.0.0.1:9092"
    SYMBOLS: str = "KRW-BTC,KRW-ETH,KRW-XRP,KRW-SOL,KRW-DOGE"   # CSV — 모듈 SYMBOLS(list)로 파싱
    # True 면 업비트 전체 KRW 마켓을 동적 구독(SYMBOLS 무시), False 면 위 정적 목록 사용.
    SUBSCRIBE_ALL_KRW: bool = False

    # ── ClickHouse ──
    CLICKHOUSE_HOST: str = "127.0.0.1"
    CLICKHOUSE_HTTP_PORT: int = 8123
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = "ch_pw"
    CLICKHOUSE_DB: str = "coin_analytics"
    # 연결/소켓 무응답 상한(초)을 명시·env 조절 가능하게 고정. send_receive 기본 300 = 라이브러리
    # 기본과 동일(연구·유지보수 대형 쿼리 회귀 방지) — 무한 행의 최종 방어는 매매 VM 절대 워치독.
    # send_receive는 소켓 비활성 타임아웃(스트리밍 수신은 패킷마다 리셋).
    CLICKHOUSE_CONNECT_TIMEOUT: int = 10
    CLICKHOUSE_SEND_RECEIVE_TIMEOUT: int = 300

    # ── 키움증권 주식 (7단계: 주식 확장) ──
    # 키움 틱 수집기(stock_kiwoom)의 수집 대상(6자리 종목코드) — 실매매 유니버스는 ML 스코어 동적 top-N(별개).
    STOCK_SYMBOLS: str = "005930,000660"   # CSV — 모듈 STOCK_SYMBOLS(list)로 파싱
    # 모의(mock) vs 실전(real) 도메인 선택. 모의계좌 검증 단계 → 기본 모의.
    KIWOOM_MOCK: bool = True
    KIWOOM_APP_KEY: str = ""       # 키움 Open API appkey
    KIWOOM_APP_SECRET: str = ""    # 키움 Open API secretkey

    # ── 거래 ──
    FEE_RATE: Decimal = Decimal("0.0005")  # 0.05%
    # 주식 매도 거래세(증권거래세+농특세). 2026 KOSPI/KOSDAQ 0.20%. 매수엔 없음·코인=0(국내주식만 적용).
    STOCK_SELL_TAX_RATE: Decimal = Decimal("0.0020")

    # ── 토스증권 Open API (데이터/조회 전용 — 체결은 KIS 모의, common/broker/kis_*) ──
    # 주식 일봉 백필 데이터 소스(백테스트 입력). client_credentials OAuth2, 클라이언트당 토큰 1개.
    TOSS_CLIENT_ID: str = ""
    TOSS_CLIENT_SECRET: str = ""

    # ── 연구 데이터 API 키 (batch 수집기 — 미설정이면 각 수집기가 실행 시점에 raise) ──
    FRED_API_KEY: str = ""   # 매크로 시계열(batch/rawdata/fred.py)
    DART_API_KEY: str = ""   # KR 펀더멘털(batch/rawdata/kr_fundamentals.py)

    # ── 한국투자증권 KIS (모의 체결 브로커 — KR+US 통합) ──
    # 계좌 1개로 국내/해외 모의 체결. OAuth2 access_token(약 24h). 토큰 재발급 횟수 제한 있어 캐시 필수.
    KIS_MOCK: bool = True
    KIS_APPKEY: str = ""          # KIS Developers appkey
    KIS_APPSECRET: str = ""       # KIS Developers appsecret
    KIS_ACCOUNT_NO: str = ""      # 계좌번호 'CANO-PRDT'(앞 8자리-상품코드 2자리)
    KIS_CONFIRM_WINDOW_SEC: int = 90   # 체결확인(잔고 diff 폴링) 최대 대기(초)

    # ── 텔레그램 매매 알림 (MTProto 사용자 계정 — Telethon StringSession) ──
    # my.telegram.org에서 api_id/api_hash 발급 → scripts/telegram_login.py 1회 실행으로 세션 발급.
    # 운영(매매 VM)은 Secret Manager `telegram-env`로 주입. 미설정이면 발송이 조용히 스킵된다.
    TELEGRAM_API_ID: str = ""        # 숫자 문자열
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_SESSION: str = ""       # StringSession(비밀)
    TELEGRAM_TARGET: str = "me"      # 수신 대상(기본 me=나에게 보내기)
    # 인바운드 /차트 봇(api/telegram_bot) — BotFather 봇 토큰. 발신용 유저세션과 분리(세션 충돌 회피).
    TELEGRAM_BOT_TOKEN: str = ""
    # 봇 명령 허용 chat_id 화이트리스트(쉼표구분). 비면 전면 거부(안전 기본). 모듈 set으로 파싱.
    TELEGRAM_ALLOWED_CHAT_IDS: str = ""

    # ── 구글 OAuth / 세션 인증 ──
    # GOOGLE_CLIENT_ID/SECRET 가 모두 설정되면 OAuth 인증 활성(모듈 AUTH_ENABLED). 미설정 시 비활성(로컬 개발).
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # 로그인 허용 이메일(allowlist). 신원확인 != 권한부여이므로 반드시 제한한다. 모듈 set으로 파싱.
    ALLOWED_EMAILS: str = ""
    # 세션 쿠키 서명 키. 운영에서는 반드시 강한 무작위 값으로 설정(openssl rand -hex 32).
    SESSION_SECRET: str = "dev-insecure-change-me"
    # 공개 도메인(Caddy). OAuth redirect_uri 구성(모듈 OAUTH_REDIRECT_URI)에 사용.
    SITE_ADDRESS: str = ""
    # 신규 계정 초기 가상자금(원)
    INITIAL_BALANCE: Decimal = Decimal("10000000")

    # ── 자동매매 전략 (규율 기반 SMA) ──
    SMA_SHORT: int = 7             # 단기 이동평균(틱 수)
    SMA_LONG: int = 25            # 장기 이동평균(틱 수)
    # 매수 1회 금액 = 현재 현금 잔고 × (신호 강도에 비례한 비율). 약한 교차→MIN, 강한 교차→MAX.
    STRATEGY_ORDER_FRACTION_MIN: Decimal = Decimal("0.05")  # 약신호 매수 비율
    STRATEGY_ORDER_FRACTION_MAX: Decimal = Decimal("0.20")  # 강신호 매수 비율
    STRATEGY_STRONG_GAP: Decimal = Decimal("0.015")  # 이 이평선 간격(비율) 이상이면 MAX 비율
    STRATEGY_COOLDOWN_SEC: int = 3600   # (계정,종목) 재진입 쿨다운(초). 1분봉 기준 60봉=1h(과매매 차단). 라이브 틱봇은 .env로 축소
    STRATEGY_STOP_LOSS_PCT: Decimal = Decimal("1.2")       # 손절 %(평단 대비)
    STRATEGY_TAKE_PROFIT_PCT: Decimal = Decimal("2.0")     # 익절 %
    STRATEGY_TRAIL_ARM_PCT: Decimal = Decimal("0.8")       # 트레일링 무장 임계 %
    STRATEGY_TRAIL_GIVEBACK_PCT: Decimal = Decimal("0.5")  # 고점 대비 되돌림 %
    STRATEGY_ENTRY_BAND: Decimal = Decimal("0.0015")  # SMA 이격 밴드(비율)
    STRATEGY_CONFIRM_TICKS: int = 2     # 확인봉 틱 수
    STRATEGY_MIN_HOLD_SEC: int = 1800   # 데드크로스 청산 최소보유(초). 1분봉 기준 30봉
    STRATEGY_WARMUP_SEC: int = 1500     # 기동 후 신규 진입/데드크로스 청산 보류(초). 1분봉 기준 25봉(=SMA_LONG)
    STRATEGY_MAX_POSITIONS: int = 10    # 계정당 동시 보유 종목 수 상한(현금 소진·과분산 방지)
    # 수수료 인지 필터(1.5단계): 진입 신호 강도(이평선 간격)가 이 비율 미만이면 진입 차단 — 약신호 과매매·수수료 출혈을 막는다.
    # 왕복 수수료(2×FEE_RATE)+슬리피지보다 충분히 큰 값으로 둔다(기본 0.5% ≫ 왕복 0.1%).
    STRATEGY_MIN_EDGE_PCT: Decimal = Decimal("0.005")
    # 데드크로스 청산 사용 여부(1.5단계). baseline에서 데드크로스가 최대 출혈원(−22.5M)이라 기본 비활성(청산은 STOP/TAKE/TRAIL만).
    STRATEGY_DEADCROSS_EXIT: bool = False
    # 업비트 최소 주문 금액(이 금액 미만 매수는 스킵). 정본은 여기(config) — sma_trader가 재-export하고 sma/disciplined가 경유 import.
    MIN_ORDER_KRW: Decimal = Decimal("5000")

    # ── 저회전 추세추종 전략 (3단계, trading/strategy/plugins/trend.py) ──
    # 일봉(상위 타임프레임) 기준 long-or-cash. 가드는 초(秒)가 아닌 **봉 수**로 둔다(일봉=1봉/일).
    TREND_SHORT: int = 10            # 단기 SMA(봉)
    TREND_LONG: int = 40            # 장기 SMA(봉). 단기>장기=상승추세→보유
    TREND_ENTRY_BAND: Decimal = Decimal("0.0")  # 히스테리시스 마진(이격 비율). 진입은 +band 초과, 청산은 -band 미만 — whipsaw·churn 차단
    TREND_VOL_TARGET: Decimal = Decimal("0.5")  # 목표 연율 변동성. 진입 비중 = min(MAX, VOL_TARGET/실현변동성)
    TREND_VOL_LOOKBACK: int = 20    # 실현변동성 산출 봉 수(일별 수익률 표준편차)
    TREND_MAX_WEIGHT: Decimal = Decimal("1.0")  # 1회 비중 상한(현물=1.0, 레버리지 불가)
    TREND_REGIME_MAX_VOL: Decimal = Decimal("2.0")  # 연율 실현변동성이 이 값 초과면 추세 무관 강제 현금(극단 레짐 필터)
    TREND_BARS_PER_YEAR: int = 365  # 변동성 연율화 계수(일봉 24/7=365). 변경 시 타임프레임과 일치시킬 것
    # 보유 중 변동성 타게팅 리밸런싱 밴드(상대): |현재비중-목표비중|/목표 > 밴드일 때만 재조정. 0=비활성(진입시 사이징만, 저회전 유지).
    TREND_REBALANCE_BAND: Decimal = Decimal("0.0")
    # 앙상블(다중 추세속도) 합성 목표비중 재조정 밴드. 채택값 0.5(BTC/ETH 6.6년 교차검증: Sharpe·일관성·저회전 최적).
    ENSEMBLE_REBALANCE_BAND: Decimal = Decimal("0.5")
    # 라이브 앙상블 운용 유니버스(채택안 = BTC/ETH). 라이브 신호 워커가 이 종목만 일봉 신호 산출. 모듈 list로 파싱.
    ENSEMBLE_SYMBOLS: str = "KRW-BTC,KRW-ETH"
    # 적응형 가중치 사용 여부(5단계). False=동일가중(현 동작 보존). True=strategy_weights 테이블 값 사용.
    # ⚠️ 적응 가중치는 과적합 위험(walk-forward에서 고정>최적화). 검증 전까지 기본 off.
    ENSEMBLE_ADAPTIVE: bool = False

    # ── 횡단면(인트라데이) 전략 (strategy/cross_sectional.py) ──
    # 매 봉 전 종목을 랭킹해 상위 N을 동일가중 long-or-cash로 보유(research §2.4 롱 다리). 회전율 억제가 1차 생존조건.
    XS_LOOKBACK: int = 30            # 랭킹 수익률 산출 룩백(봉)
    XS_TOP_N: int = 10             # 보유 상위 종목 수
    XS_REBALANCE_BAND: Decimal = Decimal("0.3")  # 목표 드리프트 밴드(저회전 — decide와 공유)
    XS_MAX_WEIGHT: Decimal = Decimal("0.2")  # 종목당 비중 상한(동일가중 1/N과 min)

    # ── 인트라데이 세션 전략 (strategy/intraday.py) ──
    # 세션(거래일) 기준 단일종목 long-or-cash, 오버나잇 미보유(마감 봉 청산). 봉=분봉 가정.
    ORB_OPENING_BARS: int = 30      # 개장 레인지 산정 봉 수(예 30분)
    MOM_SIGNAL_BARS: int = 30       # 인트라데이 모멘텀 신호 산정 봉 수
    MOM_THRESHOLD: Decimal = Decimal("0.0")       # 개장 N봉 수익률 임계(초과 시 매수)

    # ── 부하 재평가 잡 가중치 정책(5.4, backtest/reeval_weights.py) ──
    # 보수적 가드: "최적화로 향상"이 아니라 "열화 부하 자동 강등"이 목적. 동일가중 기준 소폭 이탈만 허용.
    # 각 가중치 하한/상한 = 동일가중 × 배수(부하 수 무관). floor>0 → demote≠delete(완전 제거 금지).
    ENSEMBLE_WEIGHT_FLOOR_MULT: float = 0.5  # 동일가중의 50% 이상 보장
    ENSEMBLE_WEIGHT_CAP_MULT: float = 1.5    # 동일가중의 150% 이하(독점 차단)
    # DSR(=고정구성이라 PSR) 게이트: 부하의 OOS 엣지 유의확률이 이 값 미만이면 강등(floor로). 3단계 성공기준과 동일.
    ENSEMBLE_DSR_GATE: float = 0.90
    # EWMA 평활: 신규 타깃 반영 비율(작을수록 느린 갱신 → 급변·과적합 추격 방지).
    ENSEMBLE_WEIGHT_EWMA: float = 0.2

    # ── PostgreSQL ──
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "trader"
    POSTGRES_PASSWORD: str = "trader_pw"
    POSTGRES_DB: str = "coin_trading"
    POSTGRES_CONNECT_TIMEOUT: int = 10  # 연결 수립 상한(초) — 무한 대기 방지


_settings = Settings()


def __getattr__(name: str):
    """PEP 562 — 모듈 전역에 없는 이름은 Settings 스칼라로 위임(from-import 호환)."""
    try:
        return getattr(_settings, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


# ── Kafka 토픽(비-env 상수) ──
TOPIC_TICKS = "market.ticks"
TOPIC_ORDERS = "orders"
TOPIC_EXECUTIONS = "executions"
TOPIC_SIGNALS = "strategy.signals"   # 전략 부하 → commander 신호 버스(4단계 라이브 배선)
TOPIC_STOCK_TICKS = "stock.ticks"    # 키움 실시간 주식체결(7단계 주식 확장)
TOSS_REST_BASE = "https://openapi.tossinvest.com"

# ── CSV → 리스트/집합 파생 ──
SYMBOLS = [s.strip() for s in _settings.SYMBOLS.split(",") if s.strip()]
STOCK_SYMBOLS = [s.strip() for s in _settings.STOCK_SYMBOLS.split(",") if s.strip()]
ENSEMBLE_SYMBOLS = [s.strip() for s in _settings.ENSEMBLE_SYMBOLS.split(",") if s.strip()]
TELEGRAM_ALLOWED_CHAT_IDS = {
    int(x) for x in _settings.TELEGRAM_ALLOWED_CHAT_IDS.replace(" ", "").split(",") if x.lstrip("-").isdigit()
}
ALLOWED_EMAILS = {e.strip().lower() for e in _settings.ALLOWED_EMAILS.split(",") if e.strip()}

# ── mock/real 분기 URL 파생 ──
KIWOOM_REST_BASE = "https://mockapi.kiwoom.com" if _settings.KIWOOM_MOCK else "https://api.kiwoom.com"
KIWOOM_WS_URL = (
    "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
    if _settings.KIWOOM_MOCK
    else "wss://api.kiwoom.com:10000/api/dostk/websocket"
)
KIS_REST_BASE = (
    "https://openapivts.koreainvestment.com:29443"   # 모의
    if _settings.KIS_MOCK
    else "https://openapi.koreainvestment.com:9443"  # 실전
)

# ── 인증/접속 문자열 파생 ──
AUTH_ENABLED = bool(_settings.GOOGLE_CLIENT_ID and _settings.GOOGLE_CLIENT_SECRET)
OAUTH_REDIRECT_URI = (
    f"https://{_settings.SITE_ADDRESS}/auth/callback"
    if _settings.SITE_ADDRESS
    else "http://localhost:8000/auth/callback"
)
POSTGRES_DSN = (
    f"host={_settings.POSTGRES_HOST} port={_settings.POSTGRES_PORT} user={_settings.POSTGRES_USER} "
    f"password={_settings.POSTGRES_PASSWORD} dbname={_settings.POSTGRES_DB} "
    f"connect_timeout={_settings.POSTGRES_CONNECT_TIMEOUT}"
)
