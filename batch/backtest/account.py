"""백테스트 계좌 (단일 책임: 현금·포지션 상태와 체결 적용).

수학은 portfolio.updater.apply_execution과 동일:
- 매수: 잔고 부족이면 거부. cost=price*qty+fee 차감. 평단=수수료 포함 취득단가의 가중평균.
- 매도: 보유 부족이면 거부. 현금 += price*qty-fee. 실현손익은 평단(수수료 포함) 대비 산정.
단일 계좌·동기 체결을 가정한다(라이브의 지연=0 이상화).

라이브 DB 스케일 모사: krw_balance·avg_buy_price는 NUMERIC(20,4)라 매 갱신 후 4자리로 반올림된다
(db/postgres_schema.sql). 동일 정밀도를 유지하려 현금/평단을 0.0001로 양자화(ROUND_HALF_UP=Postgres NUMERIC).
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from batch.backtest.models import ClosedTrade

QUANT_KRW = Decimal("0.0001")  # NUMERIC(20,4) — 현금·평단 저장 정밀도


def _q(x: Decimal) -> Decimal:
    return x.quantize(QUANT_KRW, rounding=ROUND_HALF_UP)


@dataclass
class Position:
    qty: Decimal
    avg: Decimal            # 수수료 포함 취득단가(portfolio.updater의 avg_buy_price와 동일 의미, 4자리)
    entry_ts: float
    entry_price: Decimal    # 진입 체결가(원가)
    entry_fee: Decimal


class BacktestAccount:
    def __init__(self, initial_cash: Decimal):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}

    def qty(self, symbol: str) -> Decimal:
        p = self.positions.get(symbol)
        return p.qty if p else Decimal(0)

    def avg(self, symbol: str) -> Decimal:
        p = self.positions.get(symbol)
        return p.avg if p else Decimal(0)

    def apply_buy(self, symbol: str, price: Decimal, qty: Decimal, fee: Decimal, ts: float) -> bool:
        """매수 체결 적용. 잔고 부족이면 False(거부, 상태 불변)."""
        cost = price * qty + fee
        if self.cash < cost:   # portfolio.updater: bal < cost → REJECTED
            return False
        self.cash = _q(self.cash - cost)
        unit_cost = _q(cost / qty)  # 수수료 포함 취득단가(NUMERIC(20,4) 모사)
        p = self.positions.get(symbol)
        if p is None or p.qty <= 0:
            self.positions[symbol] = Position(
                qty=qty, avg=unit_cost, entry_ts=ts, entry_price=price, entry_fee=fee
            )
        else:  # 추가 매수(전략상 미발생하나 평단 수학은 portfolio와 동일하게 유지)
            new_qty = p.qty + qty
            p.avg = _q((p.qty * p.avg + qty * unit_cost) / new_qty)
            p.qty = new_qty
        return True

    def apply_sell(self, symbol: str, price: Decimal, qty: Decimal, fee: Decimal, ts: float,
                   tax: Decimal = Decimal(0)) -> ClosedTrade | None:
        """매도 체결 적용. 보유 부족이면 None(거부). 성공 시 ClosedTrade(reason 미설정) 반환.

        tax(매도 거래세)는 국내주식만 >0이며 proceeds·pnl에 반영된다(기본 0 → 코인/기존 호출 무영향).
        """
        p = self.positions.get(symbol)
        if p is None or p.qty < qty:   # portfolio.updater: held < qty → REJECTED
            return None
        proceeds = price * qty - fee - tax
        self.cash = _q(self.cash + proceeds)
        cost_basis = qty * p.avg
        pnl = proceeds - cost_basis
        return_pct = (pnl / cost_basis) if cost_basis > 0 else Decimal(0)
        # 진입 수수료를 매도 수량 비례로 배분(부분매도 시 ClosedTrade별 buy_fee 중복 방지).
        # 마지막 매도(qty==p.qty)는 잔여 entry_fee 전액 → Σbuy_fee == 실제 지불 진입수수료.
        buy_fee_portion = _q(p.entry_fee * qty / p.qty)
        p.entry_fee -= buy_fee_portion
        trade = ClosedTrade(
            symbol=symbol, qty=qty, entry_price=p.entry_price, exit_price=price,
            buy_fee=buy_fee_portion, sell_fee=fee, pnl=pnl, return_pct=return_pct,
            reason="", entry_ts=p.entry_ts, exit_ts=ts, sell_tax=tax,
        )
        p.qty -= qty
        if p.qty <= 0:
            del self.positions[symbol]
        return trade

    def equity(self, last_price: dict[str, Decimal]) -> Decimal:
        """평가자산 = 현금 + Σ(보유수량 × 최신가). 최신가 미확인 종목은 제외."""
        eq = self.cash
        for sym, p in self.positions.items():
            lp = last_price.get(sym)
            if lp is not None:
                eq += p.qty * lp
        return eq
