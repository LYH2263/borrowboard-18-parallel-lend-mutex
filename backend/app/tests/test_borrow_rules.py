import pytest
from app.engines.borrow_rules import (
    can_lend, is_overdue, classify_loans, renewed_due_date,
)

def test_mutex():
    assert can_lend("available", 0)["ok"]
    assert can_lend("available", 1)["reason"] == "already_on_loan"
    assert can_lend("retired", 0)["ok"] is False

def test_overdue():
    assert is_overdue("2020-01-01", "2026-01-01", "active")
    assert not is_overdue("2020-01-01", "2026-01-01", "returned")

def test_classify():
    r = classify_loans([
        {"id": 1, "status": "active", "due_date": "2020-01-01"},
        {"id": 2, "status": "active", "due_date": "2099-01-01"},
        {"id": 3, "status": "returned", "due_date": "2020-01-01"},
    ], "2026-01-01")
    assert len(r["overdue"]) == 1 and len(r["active"]) == 1 and len(r["returned"]) == 1

ON, OFF = {"auto_renew": "on"}, {"auto_renew": "off"}

BOUNDARY_CASES = [
    # 场景名, due, today, 设置, 期望 due, 期望所在分组
    ("应还日当天未结束-不续借不过期", "2020-06-01", "2020-06-01", ON, "2020-06-01", "active"),
    ("跨日零点后-续一天仍在借", "2020-06-01", "2020-06-02", ON, "2020-06-02", "active"),
    ("开关关-跨日按逾期现算不加一天", "2020-06-01", "2020-06-02", OFF, "2020-06-01", "overdue"),
    ("开关缺省-等同关闭按逾期", "2020-06-01", "2020-06-02", {}, "2020-06-01", "overdue"),
    ("跨三天打开-一次补齐到当天", "2020-06-01", "2020-06-04", ON, "2020-06-04", "active"),
    ("已还单-过期不续不逾期", "2020-06-01", "2020-06-02", ON, "2020-06-01", "returned"),
    ("未来应还日-不动", "2099-01-01", "2020-06-02", ON, "2099-01-01", "active"),
]

@pytest.mark.parametrize("name,due,today,settings,exp_due,exp_group",
                         BOUNDARY_CASES, ids=[c[0] for c in BOUNDARY_CASES])
def test_renew_boundary(name, due, today, settings, exp_due, exp_group):
    enabled = settings.get("auto_renew") == "on"
    if exp_group == "returned":
        loan = {"id": 9, "status": "returned", "due_date": due}
    else:
        got = renewed_due_date(due, today, enabled)
        assert got == exp_due, name
        loan = {"id": 9, "status": "active", "due_date": due}
    r = classify_loans([loan], today, settings)
    groups = {"active": r["active"], "overdue": r["overdue"], "returned": r["returned"]}
    picked = [g for g, rows in groups.items() if rows]
    assert picked == [exp_group], name
    assert groups[exp_group][0]["due_date"] == exp_due, name

def test_续借计数与标记_虚拟续():
    r = classify_loans([{"id": 1, "status": "active", "due_date": "2020-06-01"}],
                       "2020-06-04", ON)
    row = r["active"][0]
    assert row["renewed"] is True

def test_续借失败抛错_不停在半路():
    # 脏 due_date：date 解析失败必须抛出，由事务整体回滚，不能半笔更新。
    with pytest.raises(ValueError):
        renewed_due_date("not-a-date", "2020-06-02", True)
    # 关闭时不解析，绝不因续借逻辑报错
    assert renewed_due_date("not-a-date", "2020-06-02", False) == "not-a-date"
