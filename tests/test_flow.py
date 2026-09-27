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

    def rev(self, batch_id: int) -> int:
        return self.s.batch_detail(batch_id)["batch"]["revision"]

    def test_full_investigation_retest_rework_and_release(self):
        batch = self.s.create_batch("operator", "operator", self.f1, "B-1", "药片", "2026-01-01", "2028-01-01")
        dev = self.s.add_deviation("operator", "operator", self.f1, batch["id"], "minor", "装量轻微偏离", self.future, batch["revision"])
        failed = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 89, 95, 105, dev["batch_id"] and self.rev(batch["id"]))
        self.assertFalse(failed["passed"])
        passed = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 99, 95, 105, self.rev(batch["id"]))
        self.assertTrue(passed["passed"])
        self.s.close_deviation("qa", "qa", dev["id"], "调整灌装参数", self.rev(batch["id"]))
        rw = self.s.plan_rework("operator", "operator", self.f1, batch["id"], "返工包装", self.rev(batch["id"]))
        self.assertEqual(1, rw["baseline_no"])
        # 返工未完成不能提前取样
        with self.assertRaises(ApiError) as early:
            self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 99, 95, 105, self.rev(batch["id"]), rw["id"])
        self.assertEqual(409, early.exception.status); self.assertIn("R1", early.exception.message)
        # 返工后不带 rework_id 也不能继续往旧基线补录
        self.s.complete_rework("operator", "operator", self.f1, rw["id"], self.rev(batch["id"]))
        with self.assertRaises(ApiError) as unbound:
            self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 99, 95, 105, self.rev(batch["id"]))
        self.assertEqual(400, unbound.exception.status); self.assertIn("rework_id", unbound.exception.message)
        # 返工完成必须重新取样，旧结果只留追溯：缺少“含量”时拒绝，说明带标识和缺少项
        with self.assertRaises(ApiError) as missing:
            self.s.decide("qa", "qa", batch["id"], "release", "尝试用旧结果放行", self.rev(batch["id"]))
        self.assertIn("R1", missing.exception.message); self.assertIn("含量", missing.exception.message)
        fresh = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, self.rev(batch["id"]), rw["id"])
        self.assertEqual(1, fresh["baseline_no"]); self.assertEqual(rw["id"], fresh["rework_id"])
        # 同一检验项目在新基线内从第 1 轮重新计轮次
        again = self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 101, 95, 105, self.rev(batch["id"]), rw["id"])
        self.assertEqual(2, again["round"]); self.assertEqual(1, again["baseline_no"])
        self.s.record_stability("lab", "lab", self.f1, batch["id"], "25C/60RH", "3m", 99, 105, self.rev(batch["id"]))
        result = self.s.decide("qa", "qa", batch["id"], "release", "返工后重新取样合格", self.rev(batch["id"]))
        self.assertEqual("released", result["batch"]["state"])
        self.assertEqual(1, len(result["batch"] and self.s.batch_detail(batch["id"])["decisions"]))
        # 历史复测继续可查，且仍标在初始基线上
        detail = self.s.batch_detail(batch["id"])
        baseline0 = [t for t in detail["tests"] if t["baseline_no"] == 0]
        self.assertEqual([89.0, 99.0], [t["result"] for t in baseline0])
        self.assertEqual([1, 2], [t["round"] for t in baseline0])

    def test_critical_block_conditional_exception_and_factory_conflict(self):
        batch = self.s.create_batch("operator", "operator", self.f1, "B-2", "胶囊", "2026-02-01", "2028-02-01")
        current = batch["revision"]
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, current)
        current = self.rev(batch["id"])
        crit = self.s.add_deviation("inspector", "inspector", self.f1, batch["id"], "critical", "无菌数据异常", self.future, current)
        current = self.rev(batch["id"])
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

    def test_repeated_rework_each_baseline_stands_alone(self):
        batch = self.s.create_batch("operator", "operator", self.f1, "B-3", "注射液", "2026-03-01", "2028-03-01")
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, batch["revision"])
        self.s.record_test("lab", "lab", self.f1, batch["id"], "有关物质", 0.5, 0, 1.0, self.rev(batch["id"]))
        # 第一次返工：含量复测合格
        rw1 = self.s.plan_rework("operator", "operator", self.f1, batch["id"], "调含量", self.rev(batch["id"]))
        self.assertEqual(1, rw1["baseline_no"])
        self.s.complete_rework("operator", "operator", self.f1, rw1["id"], self.rev(batch["id"]))
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, self.rev(batch["id"]), rw1["id"])
        # 只补了含量，缺少“有关物质”：拒绝说明必须列全缺少项
        with self.assertRaises(ApiError) as missing:
            self.s.decide("qa", "qa", batch["id"], "release", "缺项仍尝试放行", self.rev(batch["id"]))
        self.assertIn("R1", missing.exception.message); self.assertIn("有关物质", missing.exception.message)
        self.s.record_test("lab", "lab", self.f1, batch["id"], "有关物质", 0.4, 0, 1.0, self.rev(batch["id"]), rw1["id"])
        # 连续返工：第二次返工成为新基线 R2，R1 结果只留追溯
        rw2 = self.s.plan_rework("operator", "operator", self.f1, batch["id"], "再次返工", self.rev(batch["id"]))
        self.assertEqual(2, rw2["baseline_no"])
        with self.assertRaises(ApiError) as not_latest:
            self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, self.rev(batch["id"]), rw1["id"])
        self.assertEqual(409, not_latest.exception.status)
        self.assertIn("R2", not_latest.exception.message)
        self.s.complete_rework("operator", "operator", self.f1, rw2["id"], self.rev(batch["id"]))
        self.assertEqual("awaiting_resample", self.s.batch_detail(batch["id"])["batch"]["state"])
        # R2 一项都没测：两项全部缺失
        with self.assertRaises(ApiError) as none:
            self.s.decide("qa", "qa", batch["id"], "release", "空基线放行", self.rev(batch["id"]))
        self.assertIn("R2", none.exception.message); self.assertIn("含量", none.exception.message); self.assertIn("有关物质", none.exception.message)
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 90, 95, 105, self.rev(batch["id"]), rw2["id"])
        # 含量仍不合格：拒绝说明带 R2 与不合格项，而不是引用 R1/初始基线
        with self.assertRaises(ApiError) as failing:
            self.s.decide("qa", "qa", batch["id"], "conditional", "带不合格项有条件放行", self.rev(batch["id"]))
        self.assertIn("R2", failing.exception.message); self.assertIn("含量", failing.exception.message)
        self.s.record_test("lab", "lab", self.f1, batch["id"], "含量", 100, 95, 105, self.rev(batch["id"]), rw2["id"])
        self.s.record_test("lab", "lab", self.f1, batch["id"], "有关物质", 0.3, 0, 1.0, self.rev(batch["id"]), rw2["id"])
        out = self.s.decide("qa", "qa", batch["id"], "release", "R2 全部合格", self.rev(batch["id"]))
        self.assertEqual("released", out["batch"]["state"])
        # 三个基线的历史复测全部可查
        baselines = sorted({t["baseline_no"] for t in self.s.batch_detail(batch["id"])["tests"]})
        self.assertEqual([0, 1, 2], baselines)


if __name__ == "__main__": unittest.main()
