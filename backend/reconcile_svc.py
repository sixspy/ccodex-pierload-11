"""提单册与过磅票对账证明。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect, has_customs_receipt
from validators import validate_bl_match

router = APIRouter(tags=["reconcile"])


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT bl_code, declared_gross_t FROM bl_manifests ORDER BY bl_code"""
        ).fetchall()
        items = []
        for bl in rows:
            items.append(_reconcile_bl(conn, bl["bl_code"]))
        return {"items": items}


@router.get("/api/reconcile/{bl_code}")
def reconcile_one(bl_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        bl = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
        ).fetchone()
        if not bl:
            raise HTTPException(404, "提单不存在")
        return _reconcile_bl(conn, bl_code)


def _reconcile_bl(conn, bl_code: str) -> dict:
    bl = conn.execute(
        "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
    ).fetchone()
    receipt_ready = has_customs_receipt(conn, bl_code)
    tickets = conn.execute(
        """SELECT ticket_code, gross_t, status, generation FROM weigh_tickets
           WHERE bl_ref=? AND status NOT IN ('作废')""",
        (bl_code,),
    ).fetchall()
    if not tickets:
        return {
            "bl_code": bl_code,
            "declared_gross_t": bl["declared_gross_t"],
            "ticket_sum_t": 0,
            "balanced": False,
            "customs_receipt_ready": receipt_ready,
            "receipt_blocked": not receipt_ready,
            "proof_ready": False,
            "reason": "无过磅票",
            "tickets": [],
        }
    ticket_sum = round(sum(float(t["gross_t"]) for t in tickets), 3)
    declared = float(bl["declared_gross_t"])
    matched = validate_bl_match(declared, ticket_sum, tolerance=0.1)
    seal_rows = conn.execute(
        """SELECT s.seal_ref, s.ticket_generation, t.ticket_code
           FROM gate_seals s JOIN weigh_tickets t ON t.id=s.ticket_id
           WHERE t.bl_ref=?""",
        (bl_code,),
    ).fetchall()
    # 结案口径：账实平衡 + 至少一份快照 + 海关回执已到，三者缺一不可，
    # 回执未到只可挂“回执待回”，不得出证明/结案。
    proof_ok = matched and len(seal_rows) >= 1 and receipt_ready
    reason = ""
    if not receipt_ready:
        reason = "海关回执未到，对账待回执不得结案"
    elif not matched:
        reason = "账实偏差待查"
    elif not seal_rows:
        reason = "缺闸代快照"
    return {
        "bl_code": bl_code,
        "declared_gross_t": declared,
        "ticket_sum_t": ticket_sum,
        "balanced": matched,
        "customs_receipt_ready": receipt_ready,
        "receipt_blocked": not receipt_ready,
        "proof_ready": proof_ok,
        "reason": reason,
        "seal_count": len(seal_rows),
        "tickets": [dict(t) for t in tickets],
        "seals": [dict(s) for s in seal_rows],
    }


@router.get("/api/proof/ticket/{ticket_code}")
def proof_ticket(ticket_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        ticket = conn.execute(
            "SELECT * FROM weigh_tickets WHERE ticket_code=?", (ticket_code,)
        ).fetchone()
        if not ticket:
            raise HTTPException(404, "票不存在")
        bl = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (ticket["bl_ref"],)
        ).fetchone()
        seal = conn.execute(
            """SELECT * FROM gate_seals WHERE ticket_id=? ORDER BY id DESC LIMIT 1""",
            (ticket["id"],),
        ).fetchone()
        release = conn.execute(
            """SELECT * FROM gate_releases WHERE ticket_id=? AND status='已放行'""",
            (ticket["id"],),
        ).fetchone()
        overload_closed = not conn.execute(
            """SELECT id FROM overload_holds WHERE ticket_id=? AND status='开放'""",
            (ticket["id"],),
        ).fetchone()
        receipt_ready = has_customs_receipt(conn, ticket["bl_ref"])
        manifest_match = False
        if seal and bl:
            manifest_match = validate_bl_match(
                float(bl["declared_gross_t"]), float(ticket["gross_t"])
            )
        # 一致口径须含海关回执；回执未到不得 coherent
        coherent = bool(
            manifest_match
            and overload_closed
            and receipt_ready
            and (seal or release)
        )
        return {
            "ticket_code": ticket_code,
            "bl_ref": ticket["bl_ref"],
            "gross_t": ticket["gross_t"],
            "manifest_matches_bl": manifest_match,
            "has_seal": seal is not None,
            "seal_generation": seal["ticket_generation"] if seal else None,
            "released": release is not None,
            "overload_closed": overload_closed,
            "customs_receipt_ready": receipt_ready,
            "receipt_blocked": not receipt_ready,
            "coherent": coherent,
        }