"""매매 결정 분류 (단일 책임: decide() 결과 → 사람이 읽는 행동·사유).

trade_once가 종목마다 commander.decide()로 산출한 주문(또는 None=유지)과 목표비중·보유수량을
대시보드용 (action, reason)으로 번역한다. decide의 임계값을 재계산하지 않고 그 **결과의 사실관계**만
매핑하는 순수 함수라 Kafka/DB 비의존(단위 테스트 가능). action ∈ {BUY, SELL, HOLD}.
"""


def classify(order, target_w, qty):
    """decide() 결과를 (action, reason)으로 분류.

    order=(side, quantity) 또는 None(=유지), target_w=합성 목표비중(None=신호 불완전), qty=현재 보유수량.
    근거: decide는 target_w<=0이면 보유분 전량 매도, 밴드 이내·최소주문 미달이면 None을 돌려준다.
    따라서 order=None & target_w<=0 이면 보유가 없었다는 뜻(있었으면 매도됐을 것)이다.
    """
    if target_w is None:
        return ("HOLD", "신호 불완전 — 일부 추세 부하가 같은 봉으로 보고하지 않음")
    if order is not None:
        side = order[0]
        if side == "BUY":
            return ("BUY", "목표비중 상향 — 차액 매수")
        return ("SELL", "추세 반전 — 전량 청산" if target_w <= 0 else "목표비중 하향 — 차액 매도")
    # order is None → 매매 안 함(HOLD)
    if target_w <= 0:
        return ("HOLD", "추세 미진입 — 현금 유지")          # 보유 있었으면 매도됐을 것 → 무보유
    if qty > 0:
        return ("HOLD", "목표비중에 근접 — 리밸런스 밴드 내 유지(저회전)")
    return ("HOLD", "신규 진입액이 최소주문액 미만")
