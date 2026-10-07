import sqlite3
import threading
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app import seed
from app import db as dbmod
from app import main
from app.main import app, lend, return_loan, save_settings, board, loans, LendIn, SettingsIn


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BOARD_TODAY", raising=False)
    seed.init_db()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def setday(monkeypatch, d):
    monkeypatch.setenv("BOARD_TODAY", d if isinstance(d, str) else d.isoformat())


def raw():
    c = sqlite3.connect(dbmod.db_path())
    c.row_factory = sqlite3.Row
    return c


def test_跨日边界_开关关逾期现算_开关开续一天(client, monkeypatch):
    name = "跨日边界全流程"
    iid = client.post("/api/items", json={"title": "电钻", "owner": "老周"}).json()["id"]
    setday(monkeypatch, "2020-06-01")
    r = client.post(f"/api/items/{iid}/lend", json={"borrower": "邻居甲", "due_date": "2020-06-01"})
    assert r.status_code == 200, name

    # 应还日当天结束前：在借、不逾期、不动 due
    b = client.get("/api/board").json()
    assert b["counts"] == {"available": 3, "active": 2, "overdue": 0}, name
    got = [l for l in b["active"] if l["item_id"] == iid][0]
    assert got["due_date"] == "2020-06-01" and got["renewed"] is False, name

    # 零点后、开关关：逾期现算，绝不偷偷加一天（种子样例同日到期，故总数 2）
    setday(monkeypatch, "2020-06-02")
    b = client.get("/api/board").json()
    assert b["counts"]["overdue"] == 2 and b["counts"]["active"] == 0, name
    mine = [l for l in b["overdue"] if l["item_id"] == iid][0]
    assert mine["due_date"] == "2020-06-01" and mine["renewed"] is False, name

    # 打开开关：注入规则写进开关提交，同事务立刻续上
    r = client.post("/api/settings", json={"auto_renew": True})
    assert r.status_code == 200 and r.json()["applied"] >= 1, name
    b = client.get("/api/board").json()
    assert b["counts"] == {"available": 3, "active": 2, "overdue": 0}, name
    got = [l for l in b["active"] if l["item_id"] == iid][0]
    # 顶细条：从未先进逾期段再改回来——此刻它就在在借段
    assert got["due_date"] == "2020-06-02" and got["renewed"] is True and got["renewed_count"] == 1, name

    # 再过一天：看板读盘即续，滚动到当天
    setday(monkeypatch, "2020-06-03")
    b = client.get("/api/board").json()
    got = [l for l in b["active"] if l["item_id"] == iid][0]
    assert got["due_date"] == "2020-06-03" and got["renewed_count"] == 2, name
    assert b["counts"]["overdue"] == 0, name

    # 关掉开关：当天结束按逾期现算，停在 06-03，不偷偷加到 06-04
    setday(monkeypatch, "2020-06-04")
    assert client.post("/api/settings", json={"auto_renew": False}).json()["applied"] == 0, name
    b = client.get("/api/board").json()
    assert b["counts"]["overdue"] == 2, name
    mine = [l for l in b["overdue"] if l["item_id"] == iid][0]
    assert mine["due_date"] == "2020-06-03", name


def test_打开开关_2020样例立刻续上(client, monkeypatch):
    name = "开关提交即注入-2020样例"
    setday(monkeypatch, "2026-10-06")
    # 开关打开前，样例按逾期现算
    b = client.get("/api/board").json()
    assert b["counts"]["overdue"] == 1 and b["overdue"][0]["due_date"] == "2020-06-01", name

    r = client.post("/api/settings", json={"auto_renew": True}).json()
    assert r["applied"] == 1, name
    b = client.get("/api/board").json()
    assert b["counts"] == {"available": 3, "active": 1, "overdue": 0}, name
    row = b["active"][0]
    assert row["due_date"] == "2026-10-06" and row["renewed"] is True, name
    days = (date(2026, 10, 6) - date(2020, 6, 1)).days
    assert row["renewed_count"] == days, name
    # 借还记录同一套规则：样例落在在借段而不是逾期段
    lr = client.get("/api/loans").json()
    assert [l["id"] for l in lr["overdue"]] == [] and len(lr["active"]) == 1, name


def test_续借失败_due与顶细条停在失败前(client, monkeypatch):
    name = "续借失败整体回滚"
    setday(monkeypatch, "2020-06-02")
    a = client.post("/api/items", json={"title": "好电钻", "owner": "老周"}).json()["id"]
    d = client.post("/api/items", json={"title": "坏电钻", "owner": "阿强"}).json()["id"]
    client.post(f"/api/items/{a}/lend", json={"borrower": "甲", "due_date": "2020-06-01"})
    client.post(f"/api/items/{d}/lend", json={"borrower": "乙", "due_date": "2020-06-01"})

    # 直接把后一笔的 due 改成脏值，模拟续借中途失败
    c = raw()
    bad = c.execute("SELECT id FROM loans WHERE item_id=? AND status='active'", (d,)).fetchone()["id"]
    good = c.execute("SELECT id FROM loans WHERE item_id=? AND status='active'", (a,)).fetchone()["id"]
    c.execute("UPDATE loans SET due_date='not-a-date' WHERE id=?", (bad,))
    c.commit(); c.close()

    # 开关提交与续借同一事务：失败必须 5xx，开关和前一笔已做的续借一起回滚
    r = client.post("/api/settings", json={"auto_renew": True})
    assert r.status_code >= 500, name

    c = raw()
    assert c.execute("SELECT value FROM settings WHERE key='auto_renew'").fetchone() is None, name
    assert c.execute("SELECT due_date FROM loans WHERE id=?", (good,)).fetchone()["due_date"] == "2020-06-01", name
    assert c.execute("SELECT due_date FROM loans WHERE id=?", (bad,)).fetchone()["due_date"] == "not-a-date", name
    # items/loans 仍然守恒：没有 on_loan 配空行
    rows = c.execute("""SELECT items.status, COUNT(loans.id) n FROM items
                        LEFT JOIN loans ON loans.item_id=items.id AND loans.status='active'
                        GROUP BY items.id""").fetchall()
    for r_ in rows:
        assert (r_["status"] == "on_loan") == (r_["n"] == 1), name
    c.close()


def _invariants(c, name):
    # 只能留下一笔 active 或一件 available：on_loan↔恰好1笔 active，available↔0笔
    rows = c.execute("""SELECT items.id, items.status, COUNT(loans.id) n FROM items
                        LEFT JOIN loans ON loans.item_id=items.id AND loans.status='active'
                        GROUP BY items.id""").fetchall()
    for r in rows:
        if r["status"] == "on_loan":
            assert r["n"] == 1, f"{name}: item {r['id']} on_loan 但 active={r['n']}"
        elif r["status"] == "available":
            assert r["n"] == 0, f"{name}: item {r['id']} available 却挂着 active={r['n']}"
    # 同一物不得两笔 active
    dup = c.execute("""SELECT item_id, COUNT(*) n FROM loans WHERE status='active'
                       GROUP BY item_id HAVING n > 1""").fetchall()
    assert not dup, f"{name}: 同一物多笔 active {[dict(x) for x in dup]}"
    # 逾期名单与在借名单互斥且数量守恒
    n_active_loan = c.execute("SELECT COUNT(*) c FROM loans WHERE status='active'").fetchone()["c"]
    n_on_loan = c.execute("SELECT COUNT(*) c FROM items WHERE status='on_loan'").fetchone()["c"]
    assert n_active_loan == n_on_loan, f"{name}: loans active={n_active_loan} 与 items on_loan={n_on_loan} 对不上"


def test_零点三路撞车_抢借归还续借(client, monkeypatch):
    name = "零点三路并发-抢借x归还x续借"
    setday(monkeypatch, "2020-06-02")
    # 物 A 空闲（多人抢）；物 B 在借且已过应还日（续借 vs 归还 vs 抢借撞车）
    a = client.post("/api/items", json={"title": "空闲电钻", "owner": "老周"}).json()["id"]
    b = client.post("/api/items", json={"title": "在借电钻", "owner": "小陈"}).json()["id"]
    lb = client.post(f"/api/items/{b}/lend", json={"borrower": "原借用人", "due_date": "2020-06-01"}).json()["loan_id"]

    # 先打开自动续借，让读盘即触发注入
    assert client.post("/api/settings", json={"auto_renew": True}).status_code == 200

    errors = []
    jobs = []
    for k in range(12):  # 邻居抢借空闲物 A，只能成一笔
        jobs.append(lambda k=k: lend(a, LendIn(borrower=f"邻居{k}", due_date="2020-06-10")))
    for k in range(6):  # 物主点归还 B，只第一笔有效
        jobs.append(lambda: return_loan(lb))
    for k in range(6):  # B 一归还立刻抢借，与归还/续借互撞
        jobs.append(lambda k=k: lend(b, LendIn(borrower=f"夜抢{k}", due_date="2020-06-10")))
    for k in range(4):  # 自动续借注入：看板/记录读盘
        jobs.append(lambda: board())
        jobs.append(lambda: loans())
    start = threading.Barrier(len(jobs))

    def run(fn):
        def worker():
            start.wait()
            for _ in range(50):
                try:
                    fn()
                    return
                except HTTPException:
                    return  # 409/400：抢输或重复归还，正常
                except sqlite3.OperationalError:
                    time.sleep(0.01)  # 锁竞争：排队重试，绝不绕过事务
            errors.append("锁等待耗尽")
        return worker

    threads = [threading.Thread(target=run(fn)) for fn in jobs]

    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors, f"{name}: {errors}"

    c = raw()
    _invariants(c, name)
    # A 最终：on_loan 且唯一一笔 active
    na = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (a,)).fetchone()["c"]
    sa = c.execute("SELECT status FROM items WHERE id=?", (a,)).fetchone()["status"]
    assert na == 1 and sa == "on_loan", name
    # B：要么 available（归还后无人抢到），要么 on_loan 配唯一新 active，绝无空行/双 active
    _invariants(c, name)
    c.close()

    # HTTP 视图与落盘同一套数：顶细条三段相加守恒
    bj = client.get("/api/board").json()
    assert bj["counts"]["available"] == len(bj["available"]), name
    assert bj["counts"]["active"] == len(bj["active"]), name
    assert bj["counts"]["overdue"] == len(bj["overdue"]), name
    assert bj["counts"]["active"] + bj["counts"]["overdue"] == len(bj["active"]) + len(bj["overdue"]), name
