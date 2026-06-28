"""리포트 (단일 책임: 성과 요약 출력 + CSV/메타 기록).

표준출력에 요약 표를 찍고, --out 지정 시 trades.csv / equity.csv / run_meta.json을 남긴다.
run_meta.json은 재현에 필요한 설정 일체(기간·심볼·수수료·슬리피지·전략 파라미터·git 커밋)를 담는다.
"""
import csv
import json
import os
from datetime import datetime, timezone
from decimal import Decimal


def _pct(x) -> str:
    return f"{float(x) * 100:.2f}%" if x is not None else "N/A"


def _num(x) -> str:
    if x is None:
        return "N/A"
    if isinstance(x, Decimal):
        return f"{x:,.2f}"
    return str(x)


def _actual_span(engine) -> str:
    """실제 replay된 구간(첫~마지막 봉, UTC)을 ' (실제 a~b)' 형태로. 없으면 빈 문자열."""
    if engine.first_ts is None or engine.last_ts is None:
        return ""
    a = datetime.fromtimestamp(engine.first_ts, timezone.utc).strftime("%Y-%m-%d")
    b = datetime.fromtimestamp(engine.last_ts, timezone.utc).strftime("%Y-%m-%d")
    return f" (실제 {a}~{b})"


def print_summary(metrics: dict, meta: dict, engine) -> None:
    m = metrics
    print("=" * 56)
    print(f"  백테스트 결과 — {meta.get('strategy', '?')}")
    print("=" * 56)
    bar = f", bar={meta['bar_min']}m" if meta.get("bar_min") else ""
    unit = "1d" if meta.get("unit_min") == 1440 else f"{meta.get('unit_min')}m"
    src = f"{meta.get('source')} (unit={unit}{bar})"
    if meta.get("days"):
        print(f"  기간        : 최근 {meta['days']}일{_actual_span(engine)}   소스: {src}")
    else:
        print(f"  기간        : {meta.get('start') or '처음'} ~ {meta.get('end') or '끝'}   소스: {src}")
    print(f"  심볼        : {meta.get('symbols') or '전체'}")
    print(f"  봉 수       : {engine.n_bars:,}")
    print(f"  수수료율    : {meta.get('fee_rate')}   슬리피지: {meta.get('slippage_bps')}bps")
    print("-" * 56)
    print(f"  초기자산    : {_num(m['initial_equity'])} KRW")
    print(f"  최종자산    : {_num(m['final_equity'])} KRW")
    print(f"  누적수익률  : {_pct(m['total_return'])}")
    print(f"  MDD         : {_pct(m['max_drawdown'])}")
    print(f"  Sharpe(연율): {m['sharpe_annualized']:.2f}  (근사)")
    print("-" * 56)
    print(f"  거래수      : {m['num_trades']}  (승 {m['num_wins']} / 패 {m['num_losses']})")
    print(f"  승률        : {_pct(m['win_rate'])}")
    print(f"  손익비(PF)  : {('%.2f' % float(m['profit_factor'])) if m['profit_factor'] is not None else 'N/A'}")
    print(f"  평균이익    : {_num(m['avg_win'])}   평균손실: {_num(m['avg_loss'])}")
    print(f"  총수수료    : {_num(m['total_fees'])} KRW")
    print(f"  총거래세    : {_num(m['total_tax'])} KRW")
    print(f"  평균보유    : {m['avg_holding_sec']:.0f}s")
    print("=" * 56)


def write_outputs(out_dir: str, engine, metrics: dict, meta: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "qty", "entry_price", "exit_price", "buy_fee", "sell_fee", "sell_tax",
                    "pnl", "return_pct", "reason", "entry_ts", "exit_ts", "holding_sec"])
        for t in engine.closed_trades:
            w.writerow([t.symbol, t.qty, t.entry_price, t.exit_price, t.buy_fee, t.sell_fee, t.sell_tax,
                        t.pnl, t.return_pct, t.reason, t.entry_ts, t.exit_ts, t.holding_sec])

    with open(os.path.join(out_dir, "equity.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "equity"])
        for ts, eq in engine.equity_curve:
            w.writerow([ts, eq])

    with open(os.path.join(out_dir, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "metrics": metrics}, f, default=str, ensure_ascii=False, indent=2)

    print(f"[backtest] 결과 저장: {out_dir} (trades.csv, equity.csv, run_meta.json)")
