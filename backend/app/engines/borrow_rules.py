"""One active loan per item + overdue detection.

自动续借只有一条规则，续借注入（开/关设置提交）、逾期名单、在借分类、
并发事务全部走 renewed_due_date / classify_loans，禁止另写一套判断。
"""
from datetime import date, timedelta

ROLLOVER_SETTING = "auto_renew"
ON = "on"


def can_lend(item_status: str, active_loans: int) -> dict:
    if item_status != "available":
        return {"ok": False, "reason": "item_not_available"}
    if active_loans > 0:
        return {"ok": False, "reason": "already_on_loan"}
    return {"ok": True, "reason": ""}


def auto_renew_on(settings: dict) -> bool:
    return str(settings.get(ROLLOVER_SETTING, "")).strip().lower() == ON


def renewed_due_date(due_date: str, today: str, enabled: bool) -> str | None:
    """应还日当天结束（today > due_date）仍未还时，把 due_date 逐日推到昨天。

    enabled 为 False 时原样返回——逾期名单按 due_date < today 现算，
    绝不偷偷加一天。
    """
    if not enabled or not due_date:
        return due_date
    d = date.fromisoformat(due_date)
    t = date.fromisoformat(today)
    while d < t:
        d += timedelta(days=1)
    return d.isoformat()


def is_overdue(due_date: str, today: str, loan_status: str) -> bool:
    if loan_status != "active":
        return False
    return bool(due_date) and due_date < today


def classify_loans(loans: list[dict], today: str, settings: dict | None = None) -> dict:
    """按同一套规则分类。settings 打开自动续借时，过期在借单先续再分类——

    续过的单子落“在借”而不是“逾期”，顶细条因此不可能先标逾期再改回来。
    """
    enabled = auto_renew_on(settings or {})
    active, overdue, returned = [], [], []
    for L in loans:
        st = L.get("status")
        if st == "returned":
            count = L.get("renewed_count") or 0
            returned.append({**L, "renewed": bool(count), "renewed_count": count, "overdue": False})
            continue
        if st != "active":
            continue
        due = renewed_due_date(L.get("due_date"), today, enabled)
        count = L.get("renewed_count") or 0
        # 虚拟续（单元测试直调分类）或本卷已持久续过，都算续借单。
        renewed = due != L.get("due_date") or bool(count)
        row = {**L, "due_date": due, "renewed": renewed, "renewed_count": count}
        if is_overdue(due, today, st):
            overdue.append({**row, "overdue": True})
        else:
            active.append({**row, "overdue": False})
    return {"active": active, "overdue": overdue, "returned": returned}
