"""백테스트 엔진 (단일 책임: 봉 replay 구동 + 동기 체결 + 자산곡선 집계).

전략의 Broker 인터페이스를 구현한다(position_qty/avg/cash/equity/open_symbol_count/buy/sell).
매 틱: 최신가 갱신 → strategy.on_tick → 평가자산으로 틱 단위 MDD(정확)와 final_equity 갱신.
자산곡선은 균등 시간그리드(sample_sec)로 직전가 carry-forward 표본화 → Sharpe 연율화 가정과 정합.
"""
from decimal import Decimal

from backtest.account import BacktestAccount
from backtest.fills import FillModel
from backtest.models import ClosedTrade


class BacktestEngine:
    def __init__(self, account: BacktestAccount, fills: FillModel, equity_sample_sec: float = 60.0):
        self.account = account
        self.fills = fills
        self.equity_sample_sec = equity_sample_sec
        self.last_price: dict[str, Decimal] = {}
        self.closed_trades: list[ClosedTrade] = []
        self.equity_curve: list[tuple[float, Decimal]] = []  # (ts, equity) 균등그리드 표본
        self.peak_equity = account.initial_cash
        self.max_drawdown = Decimal(0)                       # 틱 단위 정확 MDD(비율)
        self.final_equity = account.initial_cash             # 마지막 틱의 실제 평가자산(수익률용)
        self.n_bars = 0
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self._last_sample_ts: float | None = None
        self._last_eq = account.initial_cash                 # 그리드 carry-forward용 직전 평가자산

    # ── Broker 인터페이스 ──
    def position_qty(self, symbol: str) -> Decimal:
        return self.account.qty(symbol)

    def position_avg(self, symbol: str) -> Decimal:
        return self.account.avg(symbol)

    def cash(self) -> Decimal:
        return self.account.cash

    def equity(self) -> Decimal:
        return self.account.equity(self.last_price)

    def open_symbol_count(self) -> int:
        return len(self.account.positions)

    def buy(self, symbol: str, qty: Decimal, ts: float) -> bool:
        mp = self.last_price.get(symbol)
        if mp is None or mp <= 0:
            return False
        fp = self.fills.fill_price("BUY", mp)
        fee = self.fills.fee(fp, qty)
        return self.account.apply_buy(symbol, fp, qty, fee, ts)

    def sell(self, symbol: str, qty: Decimal, reason: str, ts: float) -> bool:
        mp = self.last_price.get(symbol)
        if mp is None or mp <= 0:
            return False
        fp = self.fills.fill_price("SELL", mp)
        fee = self.fills.fee(fp, qty)
        trade = self.account.apply_sell(symbol, fp, qty, fee, ts)
        if trade is None:
            return False
        trade.reason = reason
        self.closed_trades.append(trade)
        return True

    # ── 구동 루프 ──
    def run(self, ticks, strategy) -> None:
        for t in ticks:
            self.last_price[t.symbol] = t.price
            self.n_bars += 1
            if self.first_ts is None:
                self.first_ts = t.ts
                self._last_sample_ts = t.ts
                init_eq = self.account.equity(self.last_price)
                self._last_eq = init_eq
                self.equity_curve.append((t.ts, init_eq))
            self.last_ts = t.ts

            strategy.on_tick(t, self)

            eq = self.account.equity(self.last_price)
            self.final_equity = eq
            if eq > self.peak_equity:
                self.peak_equity = eq
            if self.peak_equity > 0:
                dd = (self.peak_equity - eq) / self.peak_equity
                if dd > self.max_drawdown:
                    self.max_drawdown = dd
            # 균등 그리드 표본: 그리드 시점의 평가자산은 직전 틱 값(_last_eq)을 carry-forward(룩어헤드 없음)
            while t.ts - self._last_sample_ts >= self.equity_sample_sec:
                self._last_sample_ts += self.equity_sample_sec
                self.equity_curve.append((self._last_sample_ts, self._last_eq))
            self._last_eq = eq
