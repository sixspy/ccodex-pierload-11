"""回执禁完第11号案：海关回执未到，禁止完成态写入正式册票。

三红（改码前须全部失败）：
  红甲：回执缺失时闸口仍把放行完成态写入正式表（gate_releases / weigh_tickets）；
  红乙：对账把无回执票的完成态当作结案口径（proof_ready / coherent）；
  红争用：两人同时对同票抢写完成态，后到只记争用痕，不得双写正式表。

口径：甲路径——海关回执缺失则完成态正式表零改并拒写（不引入影子完成态）。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from db import FIXTURES, init_db

FIXTURE_PATH = FIXTURES / "tickets_customs_receipt_k11.json"
K11_CODE = "PL-K11"
K11_BL = "BL-2026-0111"


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin", password: str = "admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _seed_k11_bl(c: TestClient, h: dict) -> None:
    """登记第11号案提单（夹具同款口径），不产生任何海关回执。"""
    bls = {b["bl_code"] for b in c.get("/api/bl", headers=h).json()["items"]}
    if K11_BL not in bls:
        r = c.post(
            "/api/bl",
            headers=h,
            json={
                "bl_code": K11_BL,
                "cargo_desc": "回执禁完第11号案货物",
                "declared_gross_t": 43.0,
                "vessel_voyage": "回执案轮/0011",
            },
        )
        assert r.status_code == 200, r.text


def _insert_customs_receipt(bl_code: str, receipt_ref: str) -> None:
    """测试夹具：直接落海关回执表（回执登记 HTTP 口在实现期才提供）。"""
    import db
    from datetime import datetime

    with db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO customs_receipts(bl_code,receipt_ref,detail,received_at)
               VALUES (?,?,?,?)""",
            (bl_code, receipt_ref, "测试夹具回执", datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def _k11_ticket_id(c: TestClient, h: dict) -> int:
    rows = c.get("/api/tickets", headers=h).json()["items"]
    hit = next((t for t in rows if t["ticket_code"] == K11_CODE), None)
    assert hit is not None, f"种子夹具未载入 {K11_CODE}，本仓文件库夹具须跑通"
    return hit["id"]


def _has_customs_receipt(c: TestClient, h: dict, bl_code: str) -> bool:
    jobs = c.get("/api/customs/jobs", headers=h).json()["items"]
    return any(j.get("bl_code") == bl_code and j.get("status") == "回执已到" for j in jobs)


def test_k11_fixture_loaded_without_customs_receipt():
    """夹具守卫：PL-K11 由本仓文件库夹具载入，且海关回执确实缺失。"""
    raw = json.loads(Path(FIXTURE_PATH).read_text(encoding="utf-8"))
    assert any(t["ticket_code"] == K11_CODE for t in raw)
    c = TestClient(app)
    h = _auth(c)
    _seed_k11_bl(c, h)
    tid = _k11_ticket_id(c, h)
    assert tid > 0
    assert _has_customs_receipt(c, h, K11_BL) is False


def test_red_a_no_receipt_blocks_formal_completion():
    """红甲：海关回执缺失，放行完成态须拒写，正式表零改。"""
    c = TestClient(app)
    h = _auth(c)
    _seed_k11_bl(c, h)
    tid = _k11_ticket_id(c, h)
    assert _has_customs_receipt(c, h, K11_BL) is False

    res = c.post(
        "/api/gate/release",
        headers=h,
        json={"ticket_id": tid, "officer": "第11案闸口员"},
    )
    # 甲路径：拒写（409 业务冲突），不得 200 落正式完成态
    assert res.status_code == 409, res.text
    detail = res.json().get("detail", "")
    assert "回执" in detail

    # 正式表零改：无放行记录
    releases = c.get("/api/gate/releases", headers=h).json()["items"]
    assert not any(r["ticket_id"] == tid for r in releases)
    # 正式表零改：票态仍是已裁定，未被改成已放行
    ticket = c.get(f"/api/tickets/{tid}", headers=h).json()["ticket"]
    assert ticket["status"] == "已裁定"
    assert ticket["status"] != "已放行"

    # 审计须留“回执禁完”拒写痕
    audits = c.get("/api/audit?limit=500", headers=h).json()["items"]
    assert any(
        a["action"] == "customs_receipt_block"
        and K11_CODE in str(a.get("target", ""))
        for a in audits
    ), "偏离须记回执禁完痕并拒写"


def test_red_b_reconcile_must_not_close_on_missing_receipt():
    """红乙：对账不得把无回执票的完成态当结案。"""
    c = TestClient(app)
    h = _auth(c)
    _seed_k11_bl(c, h)
    tid = _k11_ticket_id(c, h)

    # 先补一份封闸快照让 proof_ready 的其它条件成立，隔离“回执”这一变量
    seal = c.post("/api/seals", headers=h, json={"ticket_id": tid})
    assert seal.status_code == 200, seal.text

    recon = c.get(f"/api/reconcile/{K11_BL}", headers=h).json()
    # 字段须存在（五页同态口径），且回执未到不得出结案
    assert "customs_receipt_ready" in recon
    assert recon["customs_receipt_ready"] is False
    assert recon["proof_ready"] is False
    assert "回执" in recon.get("reason", "") or recon.get("receipt_blocked") is True

    proof = c.get(f"/api/proof/ticket/{K11_CODE}", headers=h).json()
    assert proof["customs_receipt_ready"] is False
    assert proof["coherent"] is False


def _make_race_ticket(c: TestClient, h: dict) -> int:
    lanes = c.get("/api/lanes", headers=h).json()["items"]
    lane = next(x for x in lanes if x["lane_code"] == "LN-N-01-IN")
    made = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": K11_BL,
            "vehicle_plate": "沪K·抢票试",
            "axle_weights": [
                {"axle": 1, "w": 8.0},
                {"axle": 2, "w": 10.0},
                {"axle": 3, "w": 10.0},
                {"axle": 4, "w": 9.0},
            ],
            "ticket_code": "PL-K11-RACE",
        },
    )
    assert made.status_code == 200, made.text
    return made.json()["id"]


def test_red_contention_deterministic_lock_winner_takes_all():
    """红争用·确定路：同票完成写口被持锁时，后到只记争用痕、零写正式表。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin", "admin123")
    h_writer = _auth(c, "writer", "writer123")
    _seed_k11_bl(c, h_admin)
    race_tid = _make_race_ticket(c, h_admin)
    # 回执到达，隔离回执变量，专压抢票锁
    _insert_customs_receipt(K11_BL, "CUS-RCPT-K11-0001")

    # 先持锁：模拟先到者占据该票完成写口
    got = c.post(
        "/api/locks/acquire",
        headers=h_admin,
        json={"lock_key": f"gate:{race_tid}"},
    )
    assert got.status_code == 200, got.text

    # 后到者抢写完成态：须 409 争用，不得另写正式完成
    blocked = c.post(
        "/api/gate/release",
        headers=h_writer,
        json={"ticket_id": race_tid, "officer": "后到员"},
    )
    assert blocked.status_code == 409, blocked.text
    assert "争用" in blocked.json().get("detail", "")

    releases = c.get("/api/gate/releases", headers=h_admin).json()["items"]
    assert not any(r["ticket_id"] == race_tid for r in releases)

    contention = c.get("/api/contention", headers=h_admin).json()["items"]
    assert any(x["lock_key"] == f"gate:{race_tid}" for x in contention), contention

    ticket = c.get(f"/api/tickets/{race_tid}", headers=h_admin).json()["ticket"]
    assert ticket["status"] == "已裁定"

    # 释放后先到者可正常完成，证明锁是放行的唯一闸口
    c.post(
        "/api/locks/release",
        headers=h_admin,
        json={"lock_key": f"gate:{race_tid}"},
    )
    ok = c.post(
        "/api/gate/release",
        headers=h_admin,
        json={"ticket_id": race_tid, "officer": "先到员"},
    )
    assert ok.status_code == 200, ok.text
    releases = c.get("/api/gate/releases", headers=h_admin).json()["items"]
    formal = [r for r in releases if r["ticket_id"] == race_tid and r["status"] == "已放行"]
    assert len(formal) == 1


def test_red_contention_two_threads_same_ticket_single_formal_write():
    """红争用·第11路并发：两路并发请求打同票完成写口，只许一成一争。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin", "admin123")
    h_writer = _auth(c, "writer", "writer123")
    _seed_k11_bl(c, h_admin)
    race_tid = _make_race_ticket(c, h_admin)
    _insert_customs_receipt(K11_BL, "CUS-RCPT-K11-0002")

    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _race(headers: dict, actor: str) -> None:
        cc = TestClient(app)
        barrier.wait()
        r = cc.post(
            "/api/gate/release",
            headers=headers,
            json={"ticket_id": race_tid, "officer": actor},
        )
        results.append({"actor": actor, "status": r.status_code, "body": r.text})

    t1 = threading.Thread(target=_race, args=(h_admin, "admin"))
    t2 = threading.Thread(target=_race, args=(h_writer, "writer"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    codes = sorted(r["status"] for r in results)
    # 只许一个 200；另一个必须是干净的 409 争用，不允许 500 或双 200
    assert codes == [200, 409], results

    releases = c.get("/api/gate/releases", headers=h_admin).json()["items"]
    formal = [r for r in releases if r["ticket_id"] == race_tid and r["status"] == "已放行"]
    assert len(formal) == 1, formal

    contention = c.get("/api/contention", headers=h_admin).json()["items"]
    assert any(x["lock_key"] == f"gate:{race_tid}" for x in contention), contention

    ticket = c.get(f"/api/tickets/{race_tid}", headers=h_admin).json()["ticket"]
    assert ticket["status"] == "已放行"
