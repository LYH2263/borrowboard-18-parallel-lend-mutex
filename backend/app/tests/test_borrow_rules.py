from datetime import date
from app.engines.borrow_rules import can_lend, is_overdue, classify_loans, effective_due_date

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

def test_effective_due_boundary():
    # 跨日边界：应还日当天结束仍未还 —— due==today 不续；次日零点补一天
    assert effective_due_date("2026-10-06", "2026-10-06", True) == "2026-10-06"
    assert effective_due_date("2026-10-06", "2026-10-07", True) == "2026-10-07"
    # 跨多个午夜：欠几天补几天，结果恰好停在今天
    assert effective_due_date("2020-06-01", "2026-10-06", True) == "2026-10-06"
    # 开关关闭：原样不动，逾期由 is_overdue 现算
    assert effective_due_date("2020-06-01", "2026-10-06", False) == "2020-06-01"
    # 未来应还日不受影响；空值安全
    assert effective_due_date("2099-01-01", "2026-10-06", True) == "2099-01-01"
    assert effective_due_date("", "2026-10-06", True) is None

def test_classify_auto_renew_single_source():
    rows = [
        {"id": 1, "status": "active", "due_date": "2026-10-06"},   # 当天到期
        {"id": 2, "status": "active", "due_date": "2026-10-05"},   # 跨日，应续
        {"id": 3, "status": "active", "due_date": "2099-01-01"},   # 未到期
        {"id": 4, "status": "returned", "due_date": "2020-01-01"}, # 已还不动
    ]
    on = classify_loans(rows, "2026-10-06", auto_renew=True)
    # 续借后不得出现在逾期段：顶细条与扫名单同一套数
    assert [l["id"] for l in on["overdue"]] == []
    assert sorted(l["id"] for l in on["active"]) == [1, 2, 3]
    renewed = {l["id"]: l for l in on["active"]}
    assert renewed[2]["due_date"] == "2026-10-06" and renewed[2]["renewed"] is True
    assert renewed[1]["renewed"] is False and renewed[3]["renewed"] is False
    # 关掉开关：同一名单立刻按逾期现算，没有偷偷加一天
    off = classify_loans(rows, "2026-10-06", auto_renew=False)
    assert [l["id"] for l in off["overdue"]] == [2]
    assert off["overdue"][0]["due_date"] == "2026-10-05"
    assert sorted(l["id"] for l in off["active"]) == [1, 3]
