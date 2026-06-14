"""웹 대시보드 페이지 (단일 책임: 정적 대시보드 HTML 서빙)."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


@router.get("/", response_class=HTMLResponse)
def dashboard():
    return _INDEX.read_text(encoding="utf-8")
