from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, owner TEXT, status TEXT, data_quality TEXT
);
CREATE TABLE IF NOT EXISTS loans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, borrower TEXT, status TEXT,
  due_date TEXT, lent_at TEXT, returned_at TEXT, renewed_count INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

def init_db():
    c = connect()
    c.executescript(SCHEMA)
    # 老卷迁移：旧库没有 renewed_count 列。
    cols = {r["name"] for r in c.execute("PRAGMA table_info(loans)")}
    if "renewed_count" not in cols:
        c.execute("ALTER TABLE loans ADD COLUMN renewed_count INT NOT NULL DEFAULT 0")
    if c.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0:
        c.executemany("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)", [
            ("电钻", "老周", "available", "clean"),
            ("折叠桌", "小陈", "available", "clean"),
            ("脏数据-无主", "", "available", "dirty"),
            ("已外借样例", "阿强", "on_loan", "clean"),
        ])
        c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at,renewed_count) VALUES (?,?,?,?,?,0)",
            (4, "邻居甲", "active", "2020-06-01", "2020-05-01"),
        )
        c.execute("INSERT INTO settings(key,value) VALUES ('board_name','木色邻里板')")
        c.commit()
    c.close()
