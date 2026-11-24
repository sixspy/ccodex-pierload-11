"""海关申报空桩（可挂载接口）。

回执口径：customs_receipts 是正式完成态唯一承认的海关回执来源。
空桩页只暴露回执缺失/到达同态，不允许在回执缺失时放行任何正式完成态。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import AuthHeader, require_user, require_writer
from db import connect, has_customs_receipt, now_iso, write_audit
from models import StubJobIn

router = APIRouter(tags=["customs_stub"])


class CustomsStubIn(StubJobIn):
    bl_code: str
    row_count: int = 0


class CustomsReceiptIn(BaseModel):
    bl_code: str
    receipt_ref: str = Field(min_length=3, max_length=64)
    detail: str = ""


@router.get("/api/customs/jobs")
def list_jobs(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM customs_stub_jobs ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return {"items": [dict(r) for r in rows], "stub": True, "status": "空桩未对接"}


@router.get("/api/customs/receipts")
def list_receipts(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM customs_receipts ORDER BY bl_code"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/customs/receipts/{bl_code}")
def get_receipt(bl_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM customs_receipts WHERE bl_code=?", (bl_code,)
        ).fetchone()
        return {
            "bl_code": bl_code,
            "customs_receipt_ready": row is not None,
            "receipt": dict(row) if row else None,
        }


@router.post("/api/customs/receipt")
def register_receipt(body: CustomsReceiptIn, authorization: AuthHeader = None):
    """海关回执到达登记口：原子落正式回执表，并把同提单空桩 job 标为回执已到。"""
    user = require_writer(authorization)
    with connect() as conn:
        bl = conn.execute(
            "SELECT bl_code FROM bl_manifests WHERE bl_code=?", (body.bl_code,)
        ).fetchone()
        if not bl:
            raise HTTPException(422, "提单须先登记")
        now = now_iso()
        conn.execute(
            """INSERT INTO customs_receipts(bl_code,receipt_ref,detail,received_at)
               VALUES (?,?,?,?)
               ON CONFLICT(bl_code) DO UPDATE SET
                 receipt_ref=excluded.receipt_ref,
                 detail=excluded.detail,
                 received_at=excluded.received_at""",
            (body.bl_code, body.receipt_ref, body.detail or "海关回执到达", now),
        )
        conn.execute(
            "UPDATE customs_stub_jobs SET status='回执已到' WHERE bl_code=?",
            (body.bl_code,),
        )
        write_audit(
            conn,
            user,
            "customs_receipt_register",
            body.bl_code,
            {"receipt_ref": body.receipt_ref},
        )
        conn.commit()
        return {
            "bl_code": body.bl_code,
            "receipt_ref": body.receipt_ref,
            "customs_receipt_ready": True,
        }


@router.post("/api/customs-stub")
def enqueue_stub(body: CustomsStubIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        bl = conn.execute(
            "SELECT bl_code FROM bl_manifests WHERE bl_code=?", (body.bl_code,)
        ).fetchone()
        if not bl:
            raise HTTPException(422, "提单须先登记")
        now = now_iso()
        # 回执已到则空桩任务直接反映同态；否则明确停留“回执待回”，不得伪装完成
        status = "回执已到" if has_customs_receipt(conn, body.bl_code) else "回执待回"
        cur = conn.execute(
            """INSERT INTO customs_stub_jobs(bl_code,row_count,mode,status,detail,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                body.bl_code,
                body.row_count,
                body.mode,
                status,
                body.detail or "海关空桩预演",
                now,
            ),
        )
        write_audit(conn, user, "customs_stub", body.bl_code, {"job_id": cur.lastrowid})
        conn.commit()
        return {
            "id": cur.lastrowid,
            "status": status,
            "message": "空桩：回执已到" if status == "回执已到" else "空桩：回执待回，禁完成",
        }
