"""웹 대시보드 페이지 (단일 책임: 정적 대시보드 HTML 서빙)."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


@router.get("/", response_class=HTMLResponse)
def dashboard():
    # no-cache = 매 방문 재검증(배포 직후 구 HTML 방지) — gzip 미들웨어가 전송량은 줄임
    return HTMLResponse(_INDEX.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})
