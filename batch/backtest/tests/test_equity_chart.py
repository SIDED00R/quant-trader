"""README 자산 차트 렌더 검증 (prepare_rows/build_svg — 순수 함수, DB/네트워크 없음).

계약: 포인트<2 시리즈 제외 · TOTAL 합성 포함 · 유효한 SVG 문자열 · 빈 데이터=placeholder(파일 항상 유효) ·
라이트/다크는 각자 서피스·시리즈 색 적용.
"""
import unittest
from datetime import date

from scripts.render_equity_chart import THEMES, build_svg, prepare_rows

D = date
FX = [(D(2026, 7, 1), 1000.0), (D(2026, 7, 2), 1000.0)]


def _markets():
    return {
        "COIN": [(D(2026, 7, 1), 100.0, None), (D(2026, 7, 2), 110.0, None)],
        "KR": [(D(2026, 7, 1), 200.0, None), (D(2026, 7, 2), 210.0, None)],
        "US": [(D(2026, 7, 1), 1.0, None), (D(2026, 7, 2), 0.9, None)],
    }


class TestPrepareRows(unittest.TestCase):
    def test_total_and_normalization(self):
        rows = prepare_rows(_markets(), FX)
        self.assertEqual([r["key"] for r in rows], ["TOTAL", "COIN", "KR", "US"])
        coin = next(r for r in rows if r["key"] == "COIN")
        self.assertAlmostEqual(coin["points"][0][1], 100.0)
        self.assertAlmostEqual(coin["ret"], 10.0)
        self.assertEqual(coin["value_text"], "₩110")
        us = next(r for r in rows if r["key"] == "US")
        self.assertEqual(us["value_text"], "$1")          # USD 표기
        self.assertAlmostEqual(us["ret"], -10.0)

    def test_short_series_dropped(self):
        m = _markets()
        m["KR"] = m["KR"][:1]                             # 1포인트 → 제외
        rows = prepare_rows(m, FX)
        self.assertNotIn("KR", [r["key"] for r in rows])

    def test_us_without_fx_drops_total_only(self):
        rows = prepare_rows(_markets(), [])
        keys = [r["key"] for r in rows]
        self.assertNotIn("TOTAL", keys)
        self.assertEqual(keys, ["COIN", "KR", "US"])

    def test_empty(self):
        self.assertEqual(prepare_rows({"COIN": [], "KR": [], "US": []}, FX), [])


class TestBuildSvg(unittest.TestCase):
    def test_valid_svg_with_series(self):
        rows = prepare_rows(_markets(), FX)
        svg = build_svg(rows, THEMES["light"], "2026-07-14 01:00 UTC")
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        for label in ("전체(KRW)", "코인", "국장", "미장"):
            self.assertIn(label, svg)
        self.assertEqual(svg.count("<polyline"), 4)
        self.assertIn(THEMES["light"]["surface"], svg)
        self.assertIn(THEMES["light"]["series"]["KR"], svg)

    def test_placeholder_when_empty(self):
        svg = build_svg([], THEMES["dark"], "2026-07-14 01:00 UTC")
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        self.assertIn("데이터 수집 중", svg)
        self.assertNotIn("<polyline", svg)

    def test_themes_differ(self):
        rows = prepare_rows(_markets(), FX)
        light = build_svg(rows, THEMES["light"], "u")
        dark = build_svg(rows, THEMES["dark"], "u")
        self.assertIn(THEMES["light"]["surface"], light)
        self.assertIn(THEMES["dark"]["surface"], dark)
        self.assertNotEqual(light, dark)


if __name__ == "__main__":
    unittest.main()
