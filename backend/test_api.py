import os
from pathlib import Path
import tempfile

os.environ["DATABASE_PATH"] = tempfile.NamedTemporaryFile(delete=False).name

import pytest
from fastapi.testclient import TestClient

from main import DATABASE_PATH, app, get_connection, init_db


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_test_database():
    path = Path(DATABASE_PATH)
    if path.exists():
        path.unlink()
    init_db()
    authenticated = client.post(
        "/api/v1/auth/register",
        json={
            "name": "测试用户", "phone": "13900000000",
            "organization": "测试单位", "role": "sender", "password": "test-password",
        },
    )
    session = authenticated.json()["data"]
    with get_connection() as conn:
        conn.execute(
            "UPDATE task_handoff SET owner_user_id = ? WHERE task_id = 'TASK-001'",
            (session["user"]["id"],),
        )
    client.headers["Authorization"] = f"Bearer {session['token']}"
    yield
    client.headers.pop("Authorization", None)


def test_post_latest_history_and_events():
    normal_payload = {
        "device_id": "uniknect-001",
        "task_id": "task-demo",
        "temperature": 4.2,
        "humidity": 62.5,
        "light_raw": 120,
        "box_status": "CLOSED",
        "move_status": "STABLE",
        "temp_status": "NORMAL",
        "acc_total": 1.01,
        "motion_score": 0.2,
    }
    alert_payload = {
        **normal_payload,
        "temperature": 9.8,
        "humidity": 64.0,
        "light_raw": 3500,
        "box_status": "BOX_OPEN",
        "move_status": "IMPACT",
        "temp_status": "TEMP_ALERT",
        "acc_total": 2.8,
        "motion_score": 8.9,
    }

    response = client.post("/api/device/data", json=normal_payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True

    response = client.post("/api/device/data", json=alert_payload)
    assert response.status_code == 200
    created = response.json()
    assert created["ok"] is True
    assert created["data"]["event_type"] == "SEVERE"

    latest = client.get("/api/device/latest")
    assert latest.status_code == 200
    assert latest.json()["box_status"] == "BOX_OPEN"

    history = client.get("/api/device/history")
    assert history.status_code == 200
    assert len(history.json()) == 2

    events = client.get("/api/device/events")
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "SEVERE"


def test_register_login_me_and_logout():
    account = {
        "name": "张敏",
        "phone": "13800138000",
        "organization": "高校实验室",
        "role": "sender",
        "password": "coldchain123",
    }
    registered = client.post("/api/v1/auth/register", json=account)
    assert registered.status_code == 200
    session = registered.json()["data"]
    assert session["user"]["phone"] == account["phone"]
    assert session["user"]["role"] == "sender"
    assert "password_hash" not in session["user"]

    duplicate = client.post("/api/v1/auth/register", json=account)
    assert duplicate.status_code == 409

    bad_login = client.post(
        "/api/v1/auth/login",
        json={"phone": account["phone"], "password": "wrong-password"},
    )
    assert bad_login.status_code == 401

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"phone": account["phone"], "password": account["password"]},
    )
    assert logged_in.status_code == 200
    token = logged_in.json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    current = client.get("/api/v1/auth/me", headers=headers)
    assert current.status_code == 200
    assert current.json()["data"]["organization"] == account["organization"]

    logged_out = client.post("/api/v1/auth/logout", headers=headers)
    assert logged_out.status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_new_account_starts_empty_and_created_task_is_private():
    second = client.post(
        "/api/v1/auth/register",
        json={
            "name": "新发货人", "phone": "13700000000",
            "organization": "新实验室", "role": "sender", "password": "test-password",
        },
    ).json()["data"]
    second_headers = {"Authorization": f"Bearer {second['token']}"}

    empty = client.get("/api/v1/tasks", headers=second_headers)
    assert empty.status_code == 200
    assert empty.json()["data"] == []

    created = client.post(
        "/api/v1/tasks",
        headers=second_headers,
        json={
            "sample_name": "血液样本批次 B",
            "batch": "B-002",
            "receiver": "市医院检验科",
            "carrier": "迅达冷链",
            "expected_arrival": "2026-07-23 10:00",
            "device_id": "CLD-009",
            "box_id": "BOX-009",
            "seal_id": "SEAL-009",
            "temperature_range": "2 ~ 8℃",
        },
    )
    assert created.status_code == 200
    task = created.json()["data"]
    assert task["task_id"].startswith("WD-")
    assert task["sender"] == "新实验室"
    assert task["owner_user_id"] == second["user"]["id"]

    own_list = client.get("/api/v1/tasks", headers=second_headers).json()["data"]
    assert [item["task_id"] for item in own_list] == [task["task_id"]]
    assert client.get(f"/api/v1/tasks/{task['task_id']}").status_code == 404


def test_task_handoff_and_report():
    current = client.get("/api/task/current")
    assert current.status_code == 200
    assert current.json()["task_id"] == "TASK-001"
    assert current.json()["status"] == "待发出"

    started = client.post("/api/task/start")
    assert started.status_code == 200
    assert started.json()["task"]["status"] == "运输中"
    assert started.json()["task"]["started_at"]

    payload = {
        "device_id": "CLD-001",
        "task_id": "TASK-001",
        "temperature": 31.2,
        "humidity": 66.0,
        "light_raw": 22000,
        "box_status": "BOX_OPEN",
        "move_status": "MILD",
        "temp_status": "TEMP_ALERT",
        "acc_total": 10.2,
        "motion_score": 1.2,
    }
    response = client.post("/api/device/data", json=payload)
    assert response.status_code == 200

    events = client.get("/api/device/events")
    assert events.status_code == 200
    event_names = [item["event_name"] for item in events.json()]
    assert "开箱事件" in event_names
    assert "轻微晃动" in event_names
    assert "温度异常" in event_names

    current = client.get("/api/task/current")
    assert current.status_code == 200
    assert current.json()["status"] == "异常"

    signed = client.post("/api/task/sign")
    assert signed.status_code == 200
    assert signed.json()["task"]["status"] == "已签收"
    assert signed.json()["task"]["signed_at"]

    report = client.get("/api/task/report")
    assert report.status_code == 200
    body = report.json()
    assert body["task"]["task_id"] == "TASK-001"
    assert body["summary"]["total_records"] >= 1
    assert body["summary"]["event_count"] >= 3
    assert body["latest"]["device_id"] == "CLD-001"


def test_v1_contract_and_task_queries_use_unified_response():
    contracts = client.get("/api/v1/meta/contracts")
    assert contracts.status_code == 200
    body = contracts.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["task_statuses"] == [
        "pending_pack",
        "pending_handoff",
        "in_transit",
        "arrived",
        "signed",
        "rejected",
        "canceled",
    ]
    assert "BOX_CLOSED" in body["data"]["box_statuses"]
    assert "TEMP_OK" in body["data"]["temperature_statuses"]

    task = client.get("/api/v1/tasks/TASK-001")
    assert task.status_code == 200
    task_data = task.json()["data"]
    assert task_data["task_id"] == "TASK-001"
    assert task_data["status"] == "pending_pack"

    missing = client.get("/api/v1/tasks/DOES-NOT-EXIST")
    assert missing.status_code == 404
    assert missing.json() == {
        "code": 40401,
        "message": "task not found",
        "data": None,
    }


def test_v1_task_identifies_assigned_device():
    response = client.get("/api/v1/tasks/TASK-001")

    assert response.status_code == 200
    assert response.json()["data"]["device_id"] == "CLD-001"


def test_cors_allows_local_web_development():
    response = client.options(
        "/api/v1/tasks/TASK-001",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_device_upload_rejects_mismatched_assigned_device():
    payload = {
        "device_id": "OTHER-DEVICE",
        "task_id": "TASK-001",
        "temperature": 4.2,
        "humidity": 62.5,
        "light_raw": 120,
        "box_status": "BOX_CLOSED",
        "move_status": "STABLE",
        "temp_status": "TEMP_OK",
        "acc_total": 9.81,
        "motion_score": 0.2,
    }

    response = client.post("/api/device/data", json=payload)

    assert response.status_code == 409
    assert response.json() == {
        "ok": False,
        "error": "device does not match task",
    }
    assert client.get("/api/v1/tasks/TASK-001/telemetry/latest").json()["data"] is None


def test_v1_telemetry_and_alarms_are_filtered_and_normalized():
    normal_payload = {
        "device_id": "CLD-001",
        "task_id": "TASK-001",
        "temperature": 4.2,
        "humidity": 62.5,
        "light_raw": 120,
        "box_status": "CLOSED",
        "move_status": "STABLE",
        "temp_status": "NORMAL",
        "acc_total": 9.81,
        "motion_score": 0.2,
    }
    alert_payload = {
        **normal_payload,
        "temperature": 9.8,
        "box_status": "BOX_OPEN",
        "move_status": "IMPACT",
        "temp_status": "TEMP_ALERT",
        "motion_score": 8.9,
    }
    other_task_payload = {**normal_payload, "task_id": "OTHER-TASK"}

    assert client.post("/api/device/data", json=normal_payload).status_code == 200
    assert client.post("/api/device/data", json=alert_payload).status_code == 200
    assert client.post("/api/device/data", json=other_task_payload).status_code == 200

    latest = client.get("/api/v1/tasks/TASK-001/telemetry/latest")
    assert latest.status_code == 200
    latest_data = latest.json()["data"]
    assert latest_data["task_id"] == "TASK-001"
    assert latest_data["box_status"] == "BOX_OPEN"
    assert latest_data["temp_status"] == "TEMP_ALERT"

    history = client.get("/api/v1/tasks/TASK-001/telemetry/history?limit=10")
    assert history.status_code == 200
    history_body = history.json()["data"]
    assert history_body["limit"] == 10
    assert len(history_body["items"]) == 2
    assert history_body["items"][1]["box_status"] == "BOX_CLOSED"
    assert history_body["items"][1]["temp_status"] == "TEMP_OK"

    alarms = client.get("/api/v1/tasks/TASK-001/alarms?limit=10")
    assert alarms.status_code == 200
    alarm_types = {item["event_type"] for item in alarms.json()["data"]["items"]}
    assert {"BOX_OPEN", "IMPACT", "SEVERE", "TEMP_ALERT"}.issubset(alarm_types)

    invalid_limit = client.get("/api/v1/tasks/TASK-001/telemetry/history?limit=101")
    assert invalid_limit.status_code == 422


def test_v1_handoff_state_machine_supports_sign():
    premature_sign = client.post("/api/v1/tasks/TASK-001/sign")
    assert premature_sign.status_code == 409
    assert premature_sign.json()["code"] == 40901

    started = client.post("/api/v1/tasks/TASK-001/start")
    assert started.status_code == 200
    assert started.json()["data"]["status"] == "in_transit"
    assert started.json()["data"]["started_at"]

    signed = client.post("/api/v1/tasks/TASK-001/sign")
    assert signed.status_code == 200
    assert signed.json()["data"]["status"] == "signed"
    assert signed.json()["data"]["signed_at"]

    repeated_sign = client.post("/api/v1/tasks/TASK-001/sign")
    assert repeated_sign.status_code == 409


def test_v1_handoff_state_machine_supports_reject():
    assert client.post("/api/v1/tasks/TASK-001/start").status_code == 200
    rejected = client.post(
        "/api/v1/tasks/TASK-001/reject",
        json={"reason": "温度异常"},
    )
    assert rejected.status_code == 200
    rejected_data = rejected.json()["data"]
    assert rejected_data["status"] == "rejected"
    assert rejected_data["rejection_reason"] == "温度异常"
    assert rejected_data["rejected_at"]


def test_legacy_task_actions_clear_v1_rejection_state():
    assert client.post("/api/v1/tasks/TASK-001/start").status_code == 200
    assert client.post(
        "/api/v1/tasks/TASK-001/reject",
        json={"reason": "温度异常"},
    ).status_code == 200

    legacy_start = client.post("/api/task/start")
    assert legacy_start.status_code == 200
    assert client.get("/api/v1/tasks/TASK-001").json()["data"]["status"] == "in_transit"

    assert client.post(
        "/api/v1/tasks/TASK-001/reject",
        json={"reason": "箱体异常"},
    ).status_code == 200
    legacy_sign = client.post("/api/task/sign")
    assert legacy_sign.status_code == 200
    assert client.get("/api/v1/tasks/TASK-001").json()["data"]["status"] == "signed"


def test_v1_trace_report_uses_the_same_task_data():
    assert client.post("/api/v1/tasks/TASK-001/start").status_code == 200
    payload = {
        "device_id": "CLD-001",
        "task_id": "TASK-001",
        "temperature": 31.2,
        "humidity": 66.0,
        "light_raw": 22000,
        "box_status": "BOX_OPEN",
        "move_status": "MILD",
        "temp_status": "TEMP_ALERT",
        "acc_total": 10.2,
        "motion_score": 1.2,
    }
    assert client.post("/api/device/data", json=payload).status_code == 200

    report = client.get("/api/v1/tasks/TASK-001/trace-report")
    assert report.status_code == 200
    data = report.json()["data"]
    assert data["task"]["task_id"] == "TASK-001"
    assert data["task"]["status"] == "in_transit"
    assert data["latest"]["device_id"] == "CLD-001"
    assert data["summary"]["total_records"] == 1
    assert data["summary"]["event_count"] >= 3
    assert len(data["events"]) >= 3


def test_v1_trace_report_includes_handoff_nodes():
    assert client.post("/api/v1/tasks/TASK-001/start").status_code == 200
    assert client.post("/api/v1/tasks/TASK-001/sign").status_code == 200

    response = client.get("/api/v1/tasks/TASK-001/trace-report")

    assert response.status_code == 200
    nodes = response.json()["data"]["handoff_nodes"]
    assert [node["type"] for node in nodes] == ["started", "signed"]
    assert all(node["timestamp"] for node in nodes)
