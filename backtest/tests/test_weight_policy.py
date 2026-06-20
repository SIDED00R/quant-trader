"""부하 가중치 정책 검증 (순수 함수 compute_weights — DB/백테스트 불필요)."""
import unittest

from strategy.weight_policy import compute_weights

# alpha=1.0 → EWMA 우회(순수 타깃)로 결정적 검증. 개별 테스트에서 override.
_G = dict(floor_mult=0.5, cap_mult=1.5, dsr_gate=0.9, ewma_alpha=1.0)


def _w(scores, gates, prev=None, **over):
    return compute_weights(scores, gates, prev or {}, **{**_G, **over})


class TestComputeWeights(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(compute_weights({}, {}, {}, **_G), {})

    def test_sum_is_one(self):
        w = _w({"a": 3, "b": 1, "c": 1}, {"a": .99, "b": .99, "c": .99})
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_equal_scores_pass_gate_is_equal(self):
        w = _w({"a": 1, "b": 1, "c": 1}, {"a": .99, "b": .99, "c": .99})
        for k in "abc":
            self.assertAlmostEqual(w[k], 1 / 3)

    def test_dsr_gate_demotes_not_deletes(self):
        # c는 게이트 미달 → 강등되지만 floor 덕에 0이 아님(demote≠delete), 통과 부하보다 작아야
        w = _w({"a": 1, "b": 1, "c": 1}, {"a": .99, "b": .99, "c": .5})
        self.assertGreater(w["c"], 0.0)
        self.assertLess(w["c"], w["a"])
        self.assertAlmostEqual(w["a"], w["b"])

    def test_cap_damps_domination(self):
        # 한 부하 스코어가 압도적이어도 독점 차단(원시비중 0.98 → 충분히 감쇠) + 나머지 생존
        w = _w({"a": 100, "b": 1, "c": 1}, {"a": .99, "b": .99, "c": .99})
        self.assertLess(w["a"], 0.65)            # 원시 0.98 대비 강하게 감쇠
        self.assertGreater(w["b"], 0.0)
        self.assertAlmostEqual(w["b"], w["c"])
        self.assertEqual(max(w, key=w.get), "a")  # 그래도 최고 스코어가 최대 비중

    def test_all_gated_out_falls_back_equal(self):
        # 전부 게이트 미달 → 동일가중 폴백(거래 멈춤/쏠림 방지)
        w = _w({"a": 5, "b": 3, "c": 1}, {"a": .1, "b": .1, "c": .1})
        for k in "abc":
            self.assertAlmostEqual(w[k], 1 / 3)

    def test_ewma_pulls_toward_prev(self):
        # 느린 EWMA(alpha=0.2)는 직전(동일가중)에 가깝게 → alpha=1보다 쏠림 약함
        scores, gates = {"a": 100, "b": 1, "c": 1}, {"a": .99, "b": .99, "c": .99}
        prev = {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}
        slow = _w(scores, gates, prev, ewma_alpha=0.2)
        fast = _w(scores, gates, prev, ewma_alpha=1.0)
        self.assertLess(slow["a"], fast["a"])
        self.assertAlmostEqual(sum(slow.values()), 1.0)

    def test_floor_protects_every_load(self):
        # 어떤 부하도 완전 소멸하지 않음(모두 > 0)
        w = _w({"a": 50, "b": 0, "c": 0}, {"a": .99, "b": .99, "c": .99})
        for k in "abc":
            self.assertGreater(w[k], 0.0)


if __name__ == "__main__":
    unittest.main()
