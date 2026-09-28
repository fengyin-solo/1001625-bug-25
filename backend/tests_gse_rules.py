"""保障车辆保养规则的快速自检：直接跑 python3 tests_gse_rules.py。"""
from __future__ import annotations

from collections import Counter

from app.store import Store
from app.services import gse as g
from app.services.gse import GseService, cycle_days_for


def fresh() -> GseService:
    g.store = Store()
    return GseService()


def main() -> None:
    assert cycle_days_for("牵引车") == 60
    assert cycle_days_for("摆渡车") == 90
    assert cycle_days_for("未知车") == 90

    # 初始状态按日期同步
    svc = fresh()
    items, total = svc.list_entries()
    assert [(r["status"], r["车辆状态"]) for r in items] == [
        ("可用", "可用"), ("待保养", "待保养"), ("保养中", "保养中"),
        ("待保养", "待保养"), ("已报废", "已报废"),
    ]
    assert svc.stats() == {"在册车辆": 5, "待保养车辆": 2, "保养中车辆": 1,
                           "可用车辆": 1, "已报废车辆": 1}

    # 安排保养成功 + 重复安排幂等拒绝（车1：可用 -> 保养中）
    svc = fresh()
    e, m = svc.run_action(1, "安排保养", {"上次保养日": "2026-09-25"})
    assert e is not None and e["status"] == "保养中" and e["车辆状态"] == "保养中", m
    assert e["下次保养日"] == "2026-11-24"  # 牵引车 60 天
    e, m = svc.run_action(1, "安排保养", {"上次保养日": "2026-09-26"})
    assert e is None and "重复" in m, m

    # 报废与保养互斥（车5）
    svc = fresh()
    e, m = svc.run_action(5, "安排保养", {"上次保养日": "2026-09-25"})
    assert e is None and "报废" in m, m
    e, m = svc.run_action(5, "确认可用", {})
    assert e is None and "报废" in m, m

    # 下次保养日过期不能确认可用（车2、车4）
    svc = fresh()
    e, m = svc.run_action(2, "确认可用", {})
    assert e is None and "过期" in m, m
    e, m = svc.run_action(4, "确认可用", {})
    assert e is None and "过期" in m, m

    # 保养日异常全部失败（车2，原本待保养，校验失败状态不变）
    svc = fresh()
    bad_cases = [
        ({"上次保养日": "2026-09-20", "下次保养日": "2026-09-10"}, "填反"),
        ({}, "缺少"),
        ({"上次保养日": "2026/09/20"}, "格式无效"),
        ({"上次保养日": "2026-02-30"}, "格式无效"),
        ({"上次保养日": "2026-13-01"}, "格式无效"),
        ({"上次保养日": "2026-10-01"}, "晚于今天"),
        ({"上次保养日": "2026-09-25", "下次保养日": "not-a-date"}, "格式无效"),
    ]
    for payload, keyword in bad_cases:
        e, m = svc.run_action(2, "安排保养", payload)
        assert e is None and keyword in m, (payload, m)
        assert svc.get_entry(2)["status"] == "待保养"

    # 正常安排（摆渡车 90 天）+ 确认可用 + 重复确认
    svc = fresh()
    e, m = svc.run_action(2, "安排保养", {"上次保养日": "2026-09-25"})
    assert e is not None, m
    assert e["下次保养日"] == "2026-12-24"
    e, m = svc.run_action(2, "确认可用", {})
    assert e is not None and e["status"] == "可用" and e["车辆状态"] == "可用", m
    e, m = svc.run_action(2, "确认可用", {})
    assert e is None and "已是可用" in m, m

    # 手工下次保养日已过期：建单后仍是保养中（强状态不被日期改写），
    # 完成保养确认可用时按日期判定到期并拦下（车4）
    svc = fresh()
    e, m = svc.run_action(4, "安排保养", {"上次保养日": "2026-09-20", "下次保养日": "2026-09-22"})
    assert e is not None, m
    detail = svc.get_entry(4)
    assert detail["status"] == "保养中" and detail["车辆状态"] == "保养中"
    e, m = svc.run_action(4, "确认可用", {})
    assert e is None and "过期" in m, m

    # 登记校验
    svc = fresh()
    e, m = svc.create_entry({"车辆编号": "GSE-0003", "车辆类别": "牵引车", "适用作业": "x"})
    assert e is None and "已存在" in m, m
    e, m = svc.create_entry({"车辆编号": "GSE-0099"})
    assert e is None and "缺少必填字段" in m, m
    e, m = svc.create_entry({"车辆编号": "GSE-0099", "车辆类别": "牵引车", "适用作业": "x",
                             "上次保养日": "2026-09-20", "下次保养日": "2026-09-01"})
    assert e is None and "填反" in m, m
    e, m = svc.create_entry({"车辆编号": "GSE-0099", "车辆类别": "牵引车", "适用作业": "x",
                             "上次保养日": "bad-date"})
    assert e is None and "格式无效" in m, m
    e, m = svc.create_entry({"车辆编号": "GSE-0099", "车辆类别": "客梯车", "适用作业": "x"})
    assert e is not None and e["status"] == "待保养", m
    e, m = svc.create_entry({"车辆编号": "GSE-0100", "车辆类别": "客梯车", "适用作业": "x",
                             "上次保养日": "2026-09-01", "下次保养日": "2026-12-30"})
    assert e is not None and e["status"] == "可用" and e["车辆状态"] == "可用", m

    # 保养中车辆可报废；报废后不能再保养、不能重复报废（车3）
    svc = fresh()
    e, m = svc.run_action(3, "报废车辆", {})
    assert e is not None and e["status"] == "已报废" and e["车辆状态"] == "已报废", m
    e, m = svc.run_action(3, "安排保养", {"上次保养日": "2026-09-25"})
    assert e is None and "报废" in m, m
    e, m = svc.run_action(3, "报废车辆", {})
    assert e is None and "重复报废" in m, m

    # 列表 / 详情 / 卡片数字一致 + 状态过滤
    svc = fresh()
    items, total = svc.list_entries()
    counts = Counter(r["status"] for r in items)
    st = svc.stats()
    assert st == {"在册车辆": total, "待保养车辆": counts["待保养"],
                  "保养中车辆": counts["保养中"], "可用车辆": counts["可用"],
                  "已报废车辆": counts["已报废"]}
    row1 = next(r for r in items if r["id"] == 1)
    detail = svc.get_entry(1)
    assert row1["status"] == detail["status"] == detail["车辆状态"]
    assert svc.list_entries(status="已报废")[1] == counts["已报废"]
    assert svc.list_entries(status="待保养")[1] == counts["待保养"]

    # 到期边界：今天到期仍可用，昨天到期即待保养
    svc = fresh()
    e, _ = svc.create_entry({"车辆编号": "GSE-0201", "车辆类别": "牵引车", "适用作业": "x",
                             "上次保养日": "2026-07-30", "下次保养日": "2026-09-28"})
    assert e is not None and e["status"] == "可用"
    e, _ = svc.create_entry({"车辆编号": "GSE-0202", "车辆类别": "牵引车", "适用作业": "x",
                             "上次保养日": "2026-07-30", "下次保养日": "2026-09-27"})
    assert e is not None and e["status"] == "待保养"

    # 非法动作 / 不存在车辆
    svc = fresh()
    e, m = svc.run_action(1, "起飞", {})
    assert e is None and "不属于" in m, m
    e, m = svc.run_action(999, "安排保养", {})
    assert e is None and "不存在" in m, m

    print("ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
