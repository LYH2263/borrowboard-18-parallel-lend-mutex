"""场景化测试：跨日边界、开关注入、失败回滚、零点三路并发。

每个用例名即场景名，pytest 失败时直接打印场景；并发场景在断言消息里
汇总所有被违反的不变量。时钟通过 BOARD_TODAY 拨到跨日边界。
"""
import os
import sqlite3
import threading
import time
from datetime import date, timedelta

import httpx
import pytest

SCENARIOS = [
    "开关关闭_2020样例按逾期现算",
    "打开开关_提交即续2020样例",
    "跨日边界_应还日当天不续次日零点续一天",
    "关掉开关_不再偷偷加一天",
    "续借幂等_同一天重复扫名单不重复加",
    "续借失败_开关与due_date停在失败前",
]

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOARD_TODAY", "2026-10-06")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

def _set_today(today: str):
    os.environ["BOARD_TODAY"] = today

def _raw_due(item_id=4):
    from app.db import connect
    with connect() as c:
        row = c.execute("SELECT due_date, renewals, status FROM loans WHERE item_id=?", (item_id,)).fetchone()
        return dict(row)

def test_scenario(client, monkeypatch, scenario):
    today = "2026-10-06"

    if scenario == "开关关闭_2020样例按逾期现算":
        b = client.get("/api/board").json()
        assert b["counts"] == {"available": 3, "active": 0, "overdue": 1}, scenario
        loans = client.get("/api/loans").json()
        assert loans["overdue"][0]["due_date"] == "2020-06-01", scenario
        assert _raw_due()["due_date"] == "2020-06-01", scenario  # 没偷偷改库

    elif scenario == "打开开关_提交即续2020样例":
        r = client.post("/api/settings", json={"auto_renew": True})
        assert r.status_code == 200, scenario
        assert r.json()["renewed"] == 1, scenario
        raw = _raw_due()
        assert raw["due_date"] == today and raw["renewals"] == 1, scenario
        b = client.get("/api/board").json()
        # 顶细条与扫名单同一套数：逾期清零、在借一筆
        assert b["counts"] == {"available": 3, "active": 1, "overdue": 0}, scenario
        assert b["active"][0]["due_date"] == today and b["active"][0]["renewed"] is True, scenario
        loans = client.get("/api/loans").json()
        assert len(loans["overdue"]) == 0 and len(loans["active"]) == 1, scenario

    elif scenario == "跨日边界_应还日当天不续次日零点续一天":
        client.post("/api/settings", json={"auto_renew": True})
        client.post("/api/items/1/lend", json={"borrower": "邻居乙", "due_date": "2026-10-06"})
        # 应还日当天结束前：不续、不逾期
        _set_today("2026-10-06")
        b0 = client.get("/api/board").json()
        mine = [l for l in b0["active"] if l["item_id"] == 1][0]
        assert mine["due_date"] == "2026-10-06" and mine["renewed"] is False, scenario
        # 次日零点扫名单：due_date 自行加一天且保持 active，物品仍 on_loan
        _set_today("2026-10-07")
        b1 = client.get("/api/board").json()
        mine = [l for l in b1["active"] if l["item_id"] == 1][0]
        assert mine["due_date"] == "2026-10-07" and mine["renewed"] is True, scenario
        assert b1["counts"]["overdue"] == 0, scenario
        items = {i["id"]: i["status"] for i in client.get("/api/items").json()}
        assert items[1] == "on_loan", scenario

    elif scenario == "关掉开关_不再偷偷加一天":
        client.post("/api/settings", json={"auto_renew": True})   # 先续到 10-06
        client.post("/api/settings", json={"auto_renew": False})
        _set_today("2026-10-07")
        loans = client.get("/api/loans").json()
        # 关开关：库停在上次续到的 2026-10-06，当天结束按逾期现算
        assert loans["overdue"][0]["due_date"] == "2026-10-06", scenario
        assert _raw_due()["due_date"] == "2026-10-06", scenario

    elif scenario == "续借幂等_同一天重复扫名单不重复加":
        client.post("/api/settings", json={"auto_renew": True})
        for _ in range(5):
            client.get("/api/board")
            client.get("/api/loans")
        raw = _raw_due()
        assert raw["due_date"] == today and raw["renewals"] == 1, scenario

    elif scenario == "续借失败_开关与due_date停在失败前":
        from app import main
        from _pytest.monkeypatch import MonkeyPatch
        # 用独立 MonkeyPatch：只撤函数 mock，保留 DATA_DIR/BOARD_TODAY 环境
        mp = MonkeyPatch()

        def boom(*a, **k):
            raise RuntimeError("续借失败")
        mp.setattr(main, "effective_due_date", boom)
        # 开关提交触发续借失败：整体回滚，开关没开、due_date 不动
        r = client.post("/api/settings", json={"auto_renew": True})
        assert r.status_code == 500, scenario
        mp.undo()
        assert client.get("/api/settings").json()["auto_renew"] == "false", scenario
        assert _raw_due()["due_date"] == "2020-06-01", scenario
        # 扫名单路径失败同样停在失败前：先直接落库置开关（模拟此前已打开），
        # 再让续借规则抛错，/api/board 必须整体回滚、顶细条不出半截状态
        from app.db import connect as _connect
        with _connect() as cc:
            cc.execute("UPDATE settings SET value='true' WHERE key='auto_renew'")
            cc.commit()
        mp.setattr(main, "effective_due_date", boom)
        assert client.get("/api/board").status_code == 500, scenario
        mp.undo()
        assert _raw_due()["due_date"] == "2020-06-01", scenario

def pytest_generate_tests(metafunc):
    if "scenario" in metafunc.fixturenames:
        metafunc.parametrize("scenario", SCENARIOS, ids=SCENARIOS)

def test_零点三路并发_一笔active或一件available(tmp_path, monkeypatch):
    """邻居抢借 / 物主归还 / 扫名单自动续借 在真实 uvicorn 上同时撞库。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOARD_TODAY", "2026-10-07")
    import uvicorn
    from app.main import app
    # 开关在服务启动后打开：提交时即对 2020 样例补续
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    errors, barrier = [], threading.Barrier(16)
    future = (date(2026, 10, 7) + timedelta(days=5)).isoformat()

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as http:
            assert http.post("/api/settings", json={"auto_renew": True}).json()["renewed"] == 1

            def neighbor(k):
                barrier.wait()
                for n in range(15):
                    iid = [1, 2, 4][(k + n) % 3]
                    r = http.post(f"/api/items/{iid}/lend",
                                  json={"borrower": f"邻居{k}", "due_date": future})
                    if r.status_code not in (200, 409):
                        errors.append(f"neighbor{k} -> {r.status_code} {r.text[:120]}")

            def owner(k):
                barrier.wait()
                for _ in range(40):
                    r = http.post("/api/loans/1/return", content=b"{}",
                                  headers={"Content-Type": "application/json"})
                    if r.status_code not in (200, 400):
                        errors.append(f"owner{k} -> {r.status_code} {r.text[:120]}")

            def scanner(k):
                barrier.wait()
                for _ in range(25):
                    b = http.get("/api/board").json()
                    # 读出来的成套数字必须自洽，且开关开着不允许冒出逾期
                    if b["counts"]["active"] != len(b["active"]):
                        errors.append(f"scanner{k} active 计数两套数")
                    if b["counts"]["available"] != len(b["available"]):
                        errors.append(f"scanner{k} available 计数两套数")
                    if b["counts"]["overdue"] != 0 or b["overdue"]:
                        errors.append(f"scanner{k} 顶细条先标逾期 {b['counts']}")
                    ids = [l["id"] for l in b["active"]]
                    if len(ids) != len(set(ids)):
                        errors.append(f"scanner{k} 同一笔借据重复出现")
                    http.get("/api/loans")

            threads = [threading.Thread(target=neighbor, args=(k,)) for k in range(10)]
            threads += [threading.Thread(target=owner, args=(k,)) for k in range(2)]
            threads += [threading.Thread(target=scanner, args=(k,)) for k in range(4)]
            for th in threads: th.start()
            for th in threads: th.join()

        # 终态不变量：直接落库核对
        db = sqlite3.connect(tmp_path / "borrowboard.db")
        db.row_factory = sqlite3.Row
        items = {r["id"]: r["status"] for r in db.execute("SELECT id,status FROM items")}
        for iid, st in items.items():
            n = db.execute(
                "SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
            if n > 1:
                errors.append(f"item{iid} 出现 {n} 笔 active 借据")
            if st == "on_loan" and n == 0:
                errors.append(f"item{iid} items=on_loan 但 loans 无 active 空行")
            if st == "available" and n != 0:
                errors.append(f"item{iid} items=available 但存在 {n} 笔 active")
        db.close()
    finally:
        server.should_exit = True
        t.join(timeout=5)

    assert not errors, "场景[零点三路并发] 违反不变量:\n" + "\n".join(sorted(set(errors)))
