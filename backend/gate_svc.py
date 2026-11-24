"""闸口放行：须无开放超重，且海关回执已到（甲路径：回执缺失禁完成）。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, has_customs_receipt, now_iso, write_audit
from locks import release, require_lock
from models import GateReleaseIn
from validators import validate_no_open_overload, validate_ticket_mutable

router = APIRouter(tags=["gate"])


def _record_contention(
    conn, lock_key: str, actor: str, ticket_id: int, ticket_code: str
) -> None:
    """争用痕独立落库：新事务写入并提交，不被外层业务异常回滚。"""
    conn.commit()  # 终结可能残留的事务状态，起干净事务
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (
            lock_key,
            actor,
            "争用",
            f"同票完成态并发抢写 ticket={ticket_code}",
            now_iso(),
        ),
    )
    conn.commit()


@router.get("/api/gate/releases")
def list_releases(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT g.*, t.ticket_code, t.vehicle_plate, l.lane_code
               FROM gate_releases g
               JOIN weigh_tickets t ON t.id=g.ticket_id
               JOIN weigh_lanes l ON l.id=g.lane_id
               ORDER BY g.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/gate/release")
def release_gate(body: GateReleaseIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"gate:{body.ticket_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            ticket = conn.execute(
                """SELECT t.*, l.id lane_id FROM weigh_tickets t
                   JOIN weigh_lanes l ON l.id=t.lane_id WHERE t.id=?""",
                (body.ticket_id,),
            ).fetchone()
            if not ticket:
                raise HTTPException(404, "过磅票不存在")
            # 已放行票态说明完成写口已被先到者占据：后到只记争用痕、零改正式表。
            # 不依赖短生命周期锁的持锁窗口，覆盖“先到者已释锁、后到者才进”的并发交错。
            if ticket["status"] == "已放行":
                _record_contention(
                    conn, lock_key, user, body.ticket_id, ticket["ticket_code"]
                )
                raise HTTPException(409, "争用：同票完成态已写入，后到只记争用")
            validate_ticket_mutable(dict(ticket))
            if ticket["status"] not in ("已裁定", "旁路挂起"):
                raise HTTPException(409, "票态不允许放行")
            if ticket["status"] == "旁路挂起":
                raise HTTPException(409, "旁路挂起须先闭环")
            validate_no_open_overload(conn, body.ticket_id)

            # 回执禁完（甲路径）：海关回执缺失则完成态正式表零改并拒写。
            # 不引入影子完成态，禁止双口径混用。
            if not has_customs_receipt(conn, ticket["bl_ref"]):
                # 拒写痕独立落库：SAVEPOINT 隔离，回滚本段业务改动，保留审计
                conn.execute("SAVEPOINT receipt_block")
                write_audit(
                    conn,
                    user,
                    "customs_receipt_block",
                    ticket["ticket_code"],
                    {
                        "ticket_id": body.ticket_id,
                        "bl_ref": ticket["bl_ref"],
                        "reason": "海关回执未到禁完成",
                    },
                )
                conn.execute("RELEASE SAVEPOINT receipt_block")
                conn.commit()
                raise HTTPException(
                    409,
                    "海关回执未到，禁写正式完成态（回执禁完）",
                )

            exists = conn.execute(
                "SELECT id FROM gate_releases WHERE ticket_id=? AND status='已放行'",
                (body.ticket_id,),
            ).fetchone()
            if exists:
                raise HTTPException(409, "已放行")
            now = now_iso()
            ref = f"GR-{ticket['ticket_code']}-{now[:10]}"
            try:
                cur = conn.execute(
                    """INSERT INTO gate_releases(ticket_id,lane_id,release_ref,status,officer,released_at)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        body.ticket_id,
                        ticket["lane_id"],
                        ref,
                        "已放行",
                        body.officer,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                # 并发兜底：同票正式完成态唯一索引拦截，后到只记争用痕、零改正式表
                conn.rollback()
                _record_contention(
                    conn, lock_key, user, body.ticket_id, ticket["ticket_code"]
                )
                raise HTTPException(409, "争用：同票完成态已写入，后到只记争用")
            conn.execute(
                "UPDATE weigh_tickets SET status='已放行' WHERE id=?",
                (body.ticket_id,),
            )
            write_audit(
                conn,
                user,
                "gate_release",
                ref,
                {"release_id": cur.lastrowid, "ticket_id": body.ticket_id},
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "release_ref": ref,
                "status": "已放行",
            }
        finally:
            release(conn, lock_key, user)