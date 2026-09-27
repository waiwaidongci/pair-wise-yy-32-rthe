import sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, BatchService, Store


class BatchFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.s = BatchService(Store(Path(self.tmp.name) / "b.db"))
        self.f1 = self.s.register_factory("qa", "qa", "F1", "一厂", "CN")["id"]
        self.f2 = self.s.register_factory("qa", "qa", "F2", "二厂", "CN")["id"]
        self.future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat().replace("+00:00", "Z")

    def tearDown(self): self.s.store.close(); self.tmp.cleanup()

    def test_full_investigation_retest_rework_and_release(self):
        batch = self.s.create_batch("operator", "operator", self.f1, "B-1", "药片", "2026-01-01", "2028-01-01")
        dev = self.s.add_deviation("operator", "operator", self.f1, batch["id"], "minor", "装量轻微偏离", self.future, batch["revision"])
        failed = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 89, 95, 105, dev["batch_id"] and self.s.batch_detail(batch["id"])["batch"]["revision"])
        self.assertFalse(failed["passed"])
        self.assertIsNone(failed["rework_id"])
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        passed = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 99, 95, 105, current)
        self.assertTrue(passed["passed"]); self.assertEqual(2, passed["round"]); self.assertIsNone(passed["rework_id"])
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        self.s.close_deviation("qa", "qa", dev["id"], "调整灌装参数", current)
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        rw = self.s.plan_rework("operator", "operator", self.f1, batch["id"], "返工包装", current)
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        self.s.complete_rework("operator", "operator", self.f1, rw["id"], current)
        # Completion opens a new inspection baseline: pre-rework results no longer justify release.
        detail = self.s.batch_detail(batch["id"])
        self.assertEqual("awaiting_resample", detail["batch"]["state"])
        self.assertEqual(rw["id"], detail["current_baseline"]["baseline"]["rework_id"])
        self.assertEqual(["含量"], detail["current_baseline"]["missing"])
        current = detail["batch"]["revision"]
        self.s.record_stability("lab", "lab", self.f1, batch["id"], "25C/60RH", "3m", 99, 105, current)
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        retest = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, current)
        self.assertEqual(rw["id"], retest["rework_id"]); self.assertEqual(1, retest["round"])
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        result = self.s.decide("qa", "qa", batch["id"], "release", "返工后复测合格", current)
        self.assertEqual("released", result["batch"]["state"])
        # Historical pre-rework results remain queryable and stay attached to their own baselines.
        detail = self.s.batch_detail(batch["id"])
        self.assertEqual(1, len(result["batch"] and detail["decisions"]))
        self.assertEqual({None, rw["id"]}, {t["rework_id"] for t in detail["tests"]})
        self.assertEqual(2, len(detail["baselines"]))

    def test_release_needs_new_baseline_results_with_flagged_refusal(self):
        batch = self.s.create_batch("operator", "operator", self.f1, "B-3", "注射液", "2026-03-01", "2028-03-01")
        rev = batch["revision"]
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        self.s.record_test("lab", "lab", self.f1, batch["id"], "性状", 1, 0, 2, rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        rw = self.s.plan_rework("operator", "operator", self.f1, batch["id"], "重新灭菌", rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        self.s.complete_rework("operator", "operator", self.f1, rw["id"], rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        # Missing both items: the refusal must carry the baseline identifier and missing list.
        with self.assertRaises(ApiError) as missing:
            self.s.decide("qa", "qa", batch["id"], "release", "尝试放行", rev)
        self.assertEqual(409, missing.exception.status)
        self.assertEqual("missing_results", missing.exception.details["reason"])
        self.assertEqual(rw["id"], missing.exception.details["rework_id"])
        self.assertEqual("rework-%d" % rw["id"], missing.exception.details["baseline"])
        self.assertEqual(["含量", "性状"], missing.exception.details["missing"])
        self.assertIn(rf"返工基线 #1", missing.exception.message)
        # Conditional release is held to the same new baseline.
        with self.assertRaises(ApiError) as cond:
            self.s.decide("qa", "qa", batch["id"], "conditional", "尝试有条件放行", rev, exception_code="EX-1")
        self.assertEqual(["含量", "性状"], cond.exception.details["missing"])
        # Lab re-runs one item failing: refusal flags the remaining missing item and the failed one.
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 90, 95, 105, rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        with self.assertRaises(ApiError) as failed:
            self.s.decide("qa", "qa", batch["id"], "release", "再次尝试", rev)
        self.assertEqual("missing_results", failed.exception.details["reason"])
        self.assertEqual(["性状"], failed.exception.details["missing"])
        self.assertEqual(["含量"], failed.exception.details["failed"])
        # Once every required item is present, a failing result is reported as failed_results.
        self.s.record_test("lab", "lab", self.f1, batch["id"], "性状", 1, 0, 2, rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        with self.assertRaises(ApiError) as still_bad:
            self.s.decide("qa", "qa", batch["id"], "release", "仍不合格", rev)
        self.assertEqual("failed_results", still_bad.exception.details["reason"])
        self.assertEqual([], still_bad.exception.details["missing"])
        self.assertEqual(["含量"], still_bad.exception.details["failed"])

    def test_consecutive_reworks_keep_distinct_baselines(self):
        batch = self.s.create_batch("operator", "operator", self.f1, "B-4", "软膏", "2026-04-01", "2028-04-01")
        rev = batch["revision"]
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, rev)
        rw1 = self.s.plan_rework("operator", "operator", self.f1, batch["id"], "第一次返工",
                                 self.s.batch_detail(batch["id"])["batch"]["revision"])
        # No sampling while the rework is still planned.
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        with self.assertRaises(ApiError) as pending:
            self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, rev)
        self.assertEqual("rework_pending", pending.exception.details["reason"])
        self.assertEqual(rw1["id"], pending.exception.details["rework_id"])
        # A second rework cannot be planned on top of an unfinished one.
        with self.assertRaises(ApiError):
            self.s.plan_rework("operator", "operator", self.f1, batch["id"], "重复返工", rev)
        self.s.complete_rework("operator", "operator", self.f1, rw1["id"], rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        first = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 96, 95, 105, rev)
        self.assertEqual(rw1["id"], first["rework_id"])
        # Lab cannot backfill against the initial baseline once rework #1 is current.
        with self.assertRaises(ApiError) as stale:
            self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105,
                               self.s.batch_detail(batch["id"])["batch"]["revision"], rework_id=999)
        self.assertEqual("stale_baseline", stale.exception.details["reason"])
        # Second rework starts yet another baseline.
        rw2 = self.s.plan_rework("operator", "operator", self.f1, batch["id"], "第二次返工",
                                 self.s.batch_detail(batch["id"])["batch"]["revision"])
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        self.s.complete_rework("operator", "operator", self.f1, rw2["id"], rev)
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        second = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, rev)
        self.assertEqual(rw2["id"], second["rework_id"]); self.assertEqual(1, second["round"])
        rev = self.s.batch_detail(batch["id"])["batch"]["revision"]
        result = self.s.decide("qa", "qa", batch["id"], "release", "第二次返工后合格", rev)
        self.assertEqual("released", result["batch"]["state"])
        detail = self.s.batch_detail(batch["id"])
        seqs = [(r["id"], r["seq"]) for r in detail["rework"]]
        self.assertEqual([(rw1["id"], 1), (rw2["id"], 2)], seqs)
        self.assertEqual(3, len(detail["baselines"]))
        # Old rounds survive for traceability even though only the last baseline drove the decision.
        rounds = {(t["rework_id"], t["round"]) for t in detail["tests"]}
        self.assertIn((None, 1), rounds); self.assertIn((rw1["id"], 1), rounds); self.assertIn((rw2["id"], 1), rounds)

    def test_critical_block_conditional_exception_and_factory_conflict(self):
        batch = self.s.create_batch("operator", "operator", self.f1, "B-2", "胶囊", "2026-02-01", "2028-02-01")
        current = batch["revision"]
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, current)
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        crit = self.s.add_deviation("inspector", "inspector", self.f1, batch["id"], "critical", "无菌数据异常", self.future, current)
        current = self.s.batch_detail(batch["id"])["batch"]["revision"]
        with self.assertRaises(ApiError) as blocked:
            self.s.decide("qa", "qa", batch["id"], "release", "尝试放行", current)
        self.assertIn("关键偏差", blocked.exception.message)
        with self.assertRaises(ApiError):
            self.s.approve_exception("qa", "qa", crit["id"], "暂时接受", self.future, current)
        with self.assertRaises(ApiError):
            self.s.record_test("lab", "lab", self.f2, batch["id"], "水分", 1, 0, 2, current)
        with self.assertRaises(ApiError) as stale:
            self.s.record_test("lab", "lab", self.f1, batch["id"], "水分", 1, 0, 2, 1)
        self.assertEqual(409, stale.exception.status)


if __name__ == "__main__": unittest.main()
