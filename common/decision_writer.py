"""매매결정 기록 (단일 책임: trade_decisions INSERT).

trade_once 가 매 실행마다 (계정,종목)별 결정을 남기는 단일 경로. 매매 안 한 HOLD/SKIP 도 기록한다.
한 실행의 모든 행은 같은 decided_at 을 공유한다(호출부가 1회 산출해 전달 — 실행 단위 묶음 표시).
"""


def record_decisions(conn, decided_at, band: float, rows: list) -> None:
    """rows: [(account_id, symbol, price|None, Decision)]. 한 트랜잭션에 묶어 INSERT."""
    with conn.transaction():
        for account_id, symbol, price, d in rows:
            conn.execute(
                "INSERT INTO trade_decisions "
                "(decided_at, account_id, symbol, decision, target_w, current_w, gap, band, price, quantity, reason) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (decided_at, account_id, symbol, d.decision,
                 d.target_w, d.current_w, d.gap, band, price, d.quantity, d.reason),
            )
