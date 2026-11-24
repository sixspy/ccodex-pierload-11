import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient) -> dict:
    tok = c.post(
        "/api/login", json={"username": "admin", "password": "admin123"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def test_customs_stub_enqueue():
    c = TestClient(app)
    h = _auth(c)
    jobs = c.get("/api/customs/jobs", headers=h).json()
    assert jobs["stub"] is True
    # BL-2026-0101 在回执夹具中已到：空桩任务反映“回执已到”同态
    res = c.post(
        "/api/customs-stub",
        headers=h,
        json={"bl_code": "BL-2026-0101", "row_count": 3, "mode": "dry_run"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "回执已到"
    # BL-2026-0103 无回执：空桩任务停留“回执待回”，不得伪装完成
    pending = c.post(
        "/api/customs-stub",
        headers=h,
        json={"bl_code": "BL-2026-0103", "row_count": 1, "mode": "dry_run"},
    )
    assert pending.status_code == 200
    assert pending.json()["status"] == "回执待回"
    jobs = c.get("/api/customs/jobs", headers=h).json()["items"]
    assert len(jobs) >= 2


def test_crane_stub_enqueue():
    c = TestClient(app)
    h = _auth(c)
    ticket = c.get("/api/tickets", headers=h).json()["items"][0]
    res = c.post(
        "/api/crane-stub",
        headers=h,
        json={"ticket_id": ticket["id"], "berth_slot": "B-12"},
    )
    assert res.status_code == 200
    jobs = c.get("/api/crane/jobs", headers=h).json()["items"]
    assert len(jobs) >= 1