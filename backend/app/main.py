import os
from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app import db
from app.engines.borrow_rules import (
    ROLLOVER_SETTING, ON, auto_renew_on, can_lend, classify_loans, renewed_due_date,
)

app = FastAPI(title="Borrowboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()


def _today() -> str:
    """测例通过 BOARD_TODAY 把时钟拨到跨日边界；生产取本机当天。"""
    return os.environ.get("BOARD_TODAY") or date.today().isoformat()


def _settings(c) -> dict:
    return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}


def _apply_rollover(c, today: str, settings: dict) -> int:
    """跨日自动续借的唯一注入点。开关提交和每次看板/记录读盘都调它，
    逾期扫名单也用同一个 renewed_due_date，不存在两套数。

    逐笔 UPDATE，任何一笔失败（如脏 due_date 解析不了）整个事务回滚，
    due_date 与顶细条停在失败前。
    """
    if not auto_renew_on(settings):
        return 0
    applied = 0
    loans = c.execute("SELECT * FROM loans WHERE status='active'").fetchall()
    for L in loans:
        old = L["due_date"]
        new = renewed_due_date(old, today, True)  # 失败直接抛，触发 with txn 回滚
        if new != old:
            days = (date.fromisoformat(new) - date.fromisoformat(old)).days
            c.execute(
                "UPDATE loans SET due_date=?, renewed_count=renewed_count+? WHERE id=?",
                (new, days, L["id"]))
            applied += 1
    return applied


@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}


@app.get("/api/items")
def items():
    c = db.connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows


@app.get("/api/board")
def board():
    today = _today()
    with db.txn() as c:
        settings = _settings(c)
        _apply_rollover(c, today, settings)
        available = [dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")]
        loans = [dict(r) for r in c.execute(
            """SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id
               WHERE loans.status='active'""")]
    cls = classify_loans(loans, today, settings)
    return {
        "available": available,
        "active": cls["active"],
        "overdue": cls["overdue"],
        "counts": {"available": len(available), "active": len(cls["active"]), "overdue": len(cls["overdue"])},
        "auto_renew": auto_renew_on(settings),
        "today": today,
    }


class ItemIn(BaseModel):
    title: str
    owner: str

@app.post("/api/items")
def add_item(body: ItemIn):
    with db.txn() as c:
        cur = c.execute("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
                        (body.title, body.owner, "available", "clean"))
        iid = cur.lastrowid
    return {"id": iid}


class LendIn(BaseModel):
    borrower: str
    due_date: str

@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    # 与归还、续借抢同一把 IMMEDIATE 写锁：锁内重新读状态，杜绝两笔 active。
    with db.txn() as c:
        item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        if not item:
            raise HTTPException(404, "item")
        active = c.execute(
            "SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
        check = can_lend(item["status"], active)
        if not check["ok"]:
            raise HTTPException(409, check["reason"])
        try:
            date.fromisoformat(body.due_date)
        except ValueError:
            raise HTTPException(422, "bad_due_date")
        cur = c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (iid, body.borrower, "active", body.due_date, datetime.now(timezone.utc).isoformat()))
        c.execute("UPDATE items SET status='on_loan' WHERE id=?", (iid,))
        lid = cur.lastrowid
    return {"loan_id": lid}


@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    with db.txn() as c:
        loan = c.execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
        if not loan:
            raise HTTPException(404, "loan")
        if loan["status"] != "active":
            raise HTTPException(400, "not_active")
        c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=?",
                  (datetime.now(timezone.utc).isoformat(), lid))
        c.execute("UPDATE items SET status='available' WHERE id=? AND status='on_loan'",
                  (loan["item_id"],))
    return {"ok": True}


@app.get("/api/loans")
def loans():
    today = _today()
    with db.txn() as c:
        settings = _settings(c)
        _apply_rollover(c, today, settings)
        rows = [dict(r) for r in c.execute(
            "SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id ORDER BY loans.id DESC")]
    return {**classify_loans(rows, today, settings), "today": today, "auto_renew": auto_renew_on(settings)}


@app.get("/api/settings")
def settings():
    c = db.connect(); rows = _settings(c); c.close(); return rows


class SettingsIn(BaseModel):
    auto_renew: bool | None = None
    board_name: str | None = None

@app.post("/api/settings")
def save_settings(body: SettingsIn):
    today = _today()
    with db.txn() as c:
        if body.auto_renew is not None:
            value = ON if body.auto_renew else "off"
            c.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (ROLLOVER_SETTING, value))
        if body.board_name is not None:
            c.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("board_name", body.board_name))
        settings = _settings(c)
        # 注入规则写进开关提交：开关与续借同生共死，一个事务落盘。
        applied = _apply_rollover(c, today, settings)
    return {"settings": settings, "applied": applied, "today": today}
