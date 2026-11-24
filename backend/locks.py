"""码头锁与争用登记（写入路径嵌入）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def try_acquire(conn, lock_key: str, holder: str) -> bool:
    """原子抢票锁。

    用一条独立连接以 BEGIN IMMEDIATE 立即取库级写锁，保证两路并发下
    UNIQUE(lock_key) 判定真实串行：先到 rowcount=1 持锁，后到 rowcount=0。
    争用痕在同一原子事务内提交，不依赖业务连接，故不会被业务 HTTPException 回滚。
    传入的业务 conn 仅用于保持调用方签名兼容，不参与锁判定。
    """
    now = now_iso()
    lock_conn = connect()
    try:
        lock_conn.isolation_level = None
        lock_conn.execute("BEGIN IMMEDIATE")
        cur = lock_conn.execute(
            "INSERT OR IGNORE INTO pier_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
            (lock_key, holder, now),
        )
        won = cur.rowcount == 1
        if won:
            lock_conn.execute("COMMIT")
            return True
        row = lock_conn.execute(
            "SELECT holder FROM pier_locks WHERE lock_key=?", (lock_key,)
        ).fetchone()
        # 同持锁者重入：幂等放行，不记争用
        if row is not None and row["holder"] == holder:
            lock_conn.execute("COMMIT")
            return True
        # 后到只记争用痕：随锁事务一并提交，独立于业务事务，落库不回滚
        lock_conn.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, holder, "争用", f"持有者={row['holder'] if row else '未知'}", now),
        )
        lock_conn.execute("COMMIT")
        return False
    except Exception:
        lock_conn.execute("ROLLBACK")
        raise
    finally:
        lock_conn.close()


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM pier_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        raise HTTPException(409, "争用：后到只记争用，不写业务")


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pier_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, user)
        write_audit(conn, user, "lock_acquire", body.lock_key, {"ok": ok})
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, body.lock_key, user)
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
        return {"ok": True}