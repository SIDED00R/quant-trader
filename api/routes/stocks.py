"""주식 모의계좌 조회 (단일 책임: KIS 모의계좌 KR/US 잔고를 대시보드에 노출).

읽기 전용. 전략→모의주문 배선은 별도(주식 거래 트레이더). KIS API 직접 호출이라
응답이 느릴 수 있어(토큰+잔고+매수가능 수 콜) 대시보드는 탭 진입/수동 갱신 시에만 호출한다.
"""
from fastapi import APIRouter

from common import kis_balance

router = APIRouter(prefix="/stocks")


@router.get("/account")
def stock_account():
    """KR·US 모의계좌 잔고(현금·보유종목)."""
    return {"kr": kis_balance.kr_balance(), "us": kis_balance.us_balance()}
