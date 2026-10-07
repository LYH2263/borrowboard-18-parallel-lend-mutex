import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "borrowboard.db"

def connect():
    c = sqlite3.connect(db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    # 零点前后抢借 / 归还 / 自动续借三路撞车时，靠 BEGIN IMMEDIATE 拿全库写锁
    # 排队串行化；锁等待直接报错而不是静默重试出两套数。
    c.execute("PRAGMA busy_timeout=10000")
    return c

@contextmanager
def txn():
    """单一写入口：借出、归还、开关提交、跨日续借全部在 IMMEDIATE 事务内完成，
    提交时 items 与 loans 必须同时落盘，杜绝 on_loan 配空行或两笔 active。"""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
