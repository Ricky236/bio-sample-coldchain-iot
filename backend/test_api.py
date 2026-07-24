import os
import hashlib
import hmac
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile

os.environ["DATABASE_PATH"] = tempfile.NamedTemporaryFile(delete=False).name
os.environ["FILE_STORAGE_DIR"] = tempfile.mkdtemp(prefix="coldchain-files-")
# 单测沿用设备上报的 box_status，不启用光敏重判，避免 mock light_raw 与现场阈值冲突
os.environ["LIGHT_BOX_STATUS_OVERRIDE"] = "false"
# 单测保留 TASK-001 种子；正式库默认不种演示单
os.environ["SEED_DEMO_TASK"] = "true"
# 单测需要旧版无鉴权接口；生产默认关闭
os.environ["ENABLE_LEGACY_DEMO_API"] = "true"
os.environ["ENABLE_DEMO_DASHBOARD"] = "true"
os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ALLOW_LOCAL_SIMULATION"] = "false"
os.environ["ENABLE_LIVE_HARDWARE_PROXY"] = "false"

import pytest
from fastapi.testclient import TestClient

import main as main_module
from main import DATABASE_PATH, app, init_db, now_iso


client = TestClient(app)


def sign_device_payload(payload: dict, secret: str, timestamp: str, nonce: str) -> str:
    model_class = (
        main_module.DeviceTelemetryIn
        if "temperature" in payload
        else main_module.DeviceHeartbeatIn
    )
    normalized = model_class(**payload).model_dump(exclude_none=True)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    body_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    message = f"{payload['device_id']}.{timestamp}.{nonce}.{body_hash}"
    key = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return hmac.new(
        key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@pytest.fixture(autouse=True)
def reset_test_database():
    path = Path(DATABASE_PATH)
    if path.exists():
        path.unlink()
    storage_path = Path(os.environ["FILE_STORAGE_DIR"])
    if storage_path.exists():
        for item in storage_path.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    init_db()
    yield


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
    assert history.json()[0]["event_type"] == "SEVERE"
    assert history.json()[0]["event_display"] == "温度异常"
    assert history.json()[1]["event_display"] == "正常"

    events = client.get("/api/device/events")
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "SEVERE"


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
    assert "开箱" in event_names
    assert "轻微晃动" in event_names
    assert "温度异常" in event_names

    current = client.get("/api/task/current")
    assert current.status_code == 200
    assert current.json()["status"] == "运输中"
    assert current.json()["abnormal_count"] > 0

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
    headers = demo_admin_headers()
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
    assert body["data"]["device_statuses"] == [
        "available",
        "bound",
        "online",
        "offline",
    ]
    assert body["data"]["alarm_statuses"] == ["new", "acknowledged", "resolved"]
    assert body["data"]["handoff_confirmation_requirements"] == {
        "qr_verified": True,
        "face_verification": "optional_placeholder",
    }
    assert body["data"]["evidence_file_max_bytes"] == 5 * 1024 * 1024

    task = client.get("/api/v1/tasks/TASK-001", headers=headers)
    assert task.status_code == 200
    task_data = task.json()["data"]
    assert task_data["task_id"] == "TASK-001"
    assert task_data["status"] == "pending_pack"
    assert "abnormal_count" in task_data
    assert "latest_temperature" in task_data
    assert "latest_humidity" in task_data

    missing = client.get("/api/v1/tasks/DOES-NOT-EXIST", headers=headers)
    assert missing.status_code == 404
    assert missing.json() == {
        "code": 40401,
        "message": "task not found",
        "data": None,
    }


def test_v1_auth_register_login_me_and_logout():
    register = client.post(
        "/api/v1/auth/register",
        json={
            "username": "receiver_demo",
            "password": "receiver123",
            "role": "receiver",
            "display_name": "接收方演示账号",
        },
    )

    assert register.status_code == 200
    registered_user = register.json()["data"]["user"]
    assert registered_user["user_id"] == 1
    assert registered_user["username"] == "receiver_demo"
    assert registered_user["phone"] == "receiver_demo"
    assert registered_user["role"] == "receiver"
    assert registered_user["display_name"] == "接收方演示账号"
    assert "password" not in register.text

    duplicate = client.post(
        "/api/v1/auth/register",
        json={
            "username": "receiver_demo",
            "password": "receiver123",
            "role": "receiver",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == 40902

    bad_login = client.post(
        "/api/v1/auth/login",
        json={"username": "receiver_demo", "password": "wrong-password"},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["code"] == 40101

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "receiver_demo", "password": "receiver123"},
    )
    assert login.status_code == 200
    login_data = login.json()["data"]
    assert login_data["token"]
    assert login_data["user"]["username"] == "receiver_demo"
    assert "password" not in login.text

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login_data['token']}"},
    )
    assert me.status_code == 200
    assert me.json()["data"]["username"] == "receiver_demo"
    assert me.json()["data"]["role"] == "receiver"

    permissions = client.get(
        "/api/v1/auth/permissions",
        headers={"Authorization": f"Bearer {login_data['token']}"},
    )
    assert permissions.status_code == 200
    assert "sign_task" in permissions.json()["data"]["permissions"]

    logout = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {login_data['token']}"},
    )
    assert logout.status_code == 200
    assert logout.json()["data"]["logged_out"] is True

    expired_me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login_data['token']}"},
    )
    assert expired_me.status_code == 401


def test_v1_auth_stores_pbkdf2_password_and_hashed_token():
    register = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "secure_user",
            "name": "安全测试用户",
            "organization": "高校实验室",
            "password": "secure123",
            "role": "sender",
        },
    )
    assert register.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"phone": "secure_user", "password": "secure123"},
    )
    assert login.status_code == 200
    token = login.json()["data"]["token"]

    with closing(sqlite3.connect(DATABASE_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        user_row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            ("secure_user",),
        ).fetchone()
        token_row = conn.execute("SELECT * FROM auth_tokens").fetchone()

    assert user_row["password_hash"].startswith("pbkdf2_sha256$")
    assert "secure123" not in user_row["password_hash"]
    assert token_row["token"] != token
    assert len(token_row["token"]) == 64

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_v1_auth_refresh_rotates_token():
    client.post(
        "/api/v1/auth/register",
        json={
            "phone": "receiver_refresh",
            "name": "刷新测试接收方",
            "organization": "市医院检验科",
            "password": "receiver123",
            "role": "receiver",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"phone": "receiver_refresh", "password": "receiver123"},
    )
    assert login.status_code == 200
    old_token = login.json()["data"]["token"]

    refresh = client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {old_token}"},
    )

    assert refresh.status_code == 200
    new_token = refresh.json()["data"]["token"]
    assert new_token
    assert new_token != old_token

    old_me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert old_me.status_code == 401

    new_me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert new_me.status_code == 200
    assert new_me.json()["data"]["phone"] == "receiver_refresh"


def test_v1_public_registration_cannot_create_admin():
    response = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "forbidden_admin",
            "name": "非法管理员",
            "organization": "未知单位",
            "role": "admin",
            "password": "password123",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == 40303
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"phone": "forbidden_admin", "password": "password123"},
        ).status_code
        == 401
    )


def test_v1_login_rate_limit_returns_429_without_exposing_credentials():
    for _ in range(main_module.LOGIN_RATE_LIMIT):
        response = client.post(
            "/api/v1/auth/login",
            json={"phone": "rate_limited_account", "password": "wrong-password"},
        )
        assert response.status_code == 401
    limited = client.post(
        "/api/v1/auth/login",
        json={"phone": "rate_limited_account", "password": "wrong-password"},
    )
    assert limited.status_code == 429
    assert limited.json()["code"] == 42901
    assert int(limited.headers["Retry-After"]) >= 1
    assert "wrong-password" not in limited.text


def register_and_login(username: str, role: str, organization: str = "测试单位"):
    previous_admin_setting = main_module.ALLOW_ADMIN_SELF_REGISTER
    if role == "admin":
        main_module.ALLOW_ADMIN_SELF_REGISTER = True
    try:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "name": username,
                "phone": username,
                "organization": organization,
                "role": role,
                "password": "password123",
            },
        )
    finally:
        main_module.ALLOW_ADMIN_SELF_REGISTER = previous_admin_setting
    assert registered.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"phone": username, "password": "password123"},
    )
    assert login.status_code == 200
    return login.json()["data"]["token"], login.json()["data"]["user"]


def demo_admin_headers():
    token, _ = register_and_login("admin_test", "admin", "组委会")
    return {"Authorization": f"Bearer {token}"}


def provision_demo_device(secret: str = "demo-device-secret"):
    headers = demo_admin_headers()
    registered = client.post(
        "/api/v1/devices",
        headers=headers,
        json={"device_id": "CLD-001", "device_secret": secret},
    )
    assert registered.status_code == 200
    bound = client.post(
        "/api/v1/devices/CLD-001/bind",
        headers=headers,
        json={"task_id": "TASK-001"},
    )
    assert bound.status_code == 200
    return headers, secret


def post_signed(path: str, payload: dict, secret: str, nonce: str):
    timestamp = now_iso()
    return client.post(
        path,
        json=payload,
        headers={
            "X-Device-Id": payload["device_id"],
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": sign_device_payload(payload, secret, timestamp, nonce),
        },
    )


def verify_handoff_qr(
    task_id: str,
    handoff_id: str,
    issuer_headers: dict,
    recipient_headers: dict,
):
    generated = client.post(
        f"/api/v1/tasks/{task_id}/qr-tokens",
        headers=issuer_headers,
        json={
            "action": "handoff_confirm",
            "handoff_id": handoff_id,
            "ttl_seconds": 60,
        },
    )
    assert generated.status_code == 200
    verified = client.post(
        "/api/v1/qr-tokens/verify",
        headers=recipient_headers,
        json={"token": generated.json()["data"]["token"]},
    )
    assert verified.status_code == 200
    return verified.json()["data"]


def test_v1_task_list_is_empty_for_new_sender_and_can_create_task():
    token, user = register_and_login("sender_13800000001", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {token}"}

    empty_list = client.get("/api/v1/tasks", headers=headers)
    assert empty_list.status_code == 200
    assert empty_list.json()["data"]["items"] == []

    created = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "create-task-001"},
        json={
            "sample_name": "血液样本批次 A",
            "batch": "B-20260722-01",
            "receiver": "市医院检验科",
            "carrier": "迅达冷链",
            "expected_arrival": "2026-07-23T10:00:00+08:00",
            "box_id": "BOX-A12",
            "seal_id": "SEAL-8891",
            "temperature_min": 2.0,
            "temperature_max": 8.0,
        },
    )

    assert created.status_code == 200
    task = created.json()["data"]
    assert task["task_id"].startswith("WD-")
    assert task["sample_name"] == "血液样本批次 A"
    assert task["sender"] == user["organization"]
    assert task["status"] == "pending_pack"
    assert task["owner_user_id"] == user["user_id"]

    repeated = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "create-task-001"},
        json={
            "sample_name": "不应重复创建",
            "batch": "B-REPEAT",
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["task_id"] == task["task_id"]

    task_list = client.get("/api/v1/tasks", headers=headers)
    assert task_list.status_code == 200
    assert [item["task_id"] for item in task_list.json()["data"]["items"]] == [
        task["task_id"]
    ]


def test_v1_task_list_supports_updated_after_filter():
    token, _ = register_and_login("sender_updated_after", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {token}"}

    old_task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "较早任务"},
    )
    assert old_task.status_code == 200
    new_task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "较新任务"},
    )
    assert new_task.status_code == 200

    with closing(sqlite3.connect(DATABASE_PATH)) as conn, conn:
        conn.execute(
            "UPDATE task_handoff SET updated_at = ? WHERE task_id = ?",
            ("2026-07-22T10:00:00+08:00", old_task.json()["data"]["task_id"]),
        )
        conn.execute(
            "UPDATE task_handoff SET updated_at = ? WHERE task_id = ?",
            ("2026-07-22T11:00:00+08:00", new_task.json()["data"]["task_id"]),
        )

    response = client.get(
        "/api/v1/tasks?updated_after=2026-07-22T10:30:00+08:00",
        headers=headers,
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["task_id"] for item in items] == [new_task.json()["data"]["task_id"]]


def test_v1_legacy_text_owner_id_remains_visible_to_owner():
    token, user = register_and_login("sender_legacy_owner", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "旧数据兼容任务"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]

    with closing(sqlite3.connect(DATABASE_PATH)) as conn, conn:
        conn.execute(
            "UPDATE task_handoff SET owner_user_id = ? WHERE task_id = ?",
            (str(user["user_id"]), task_id),
        )

    detail = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["owner_user_id"] == user["user_id"]

    latest = client.get(
        f"/api/v1/tasks/{task_id}/telemetry/latest",
        headers=headers,
    )
    assert latest.status_code == 200


def test_v1_task_permission_assign_cancel_and_precheck():
    sender_token, _ = register_and_login("sender_13800000002", "sender", "高校实验室")
    other_token, _ = register_and_login("sender_13800000003", "sender", "其他实验室")
    carrier_token, carrier = register_and_login(
        "carrier_13800000004", "carrier", "迅达冷链"
    )
    receiver_token, receiver = register_and_login(
        "receiver_13800000005", "receiver", "市医院检验科"
    )
    sender_headers = {"Authorization": f"Bearer {sender_token}"}

    created = client.post(
        "/api/v1/tasks",
        headers=sender_headers,
        json={
            "sample_name": "血液样本批次 B",
            "batch": "B-20260722-02",
            "temperature_min": 2.0,
            "temperature_max": 8.0,
        },
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]

    forbidden = client.get(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert forbidden.status_code == 404

    patched = client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=sender_headers,
        json={"sample_name": "血液样本批次 B-修改", "box_id": "BOX-B01"},
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["sample_name"] == "血液样本批次 B-修改"
    assert patched.json()["data"]["box_id"] == "BOX-B01"

    assigned = client.post(
        f"/api/v1/tasks/{task_id}/assign",
        headers=sender_headers,
        json={
            "carrier_user_id": carrier["user_id"],
            "receiver_user_id": receiver["user_id"],
        },
    )
    assert assigned.status_code == 200
    assert assigned.json()["data"]["carrier_user_id"] == carrier["user_id"]
    assert assigned.json()["data"]["receiver_user_id"] == receiver["user_id"]

    carrier_list = client.get(
        "/api/v1/tasks",
        headers={"Authorization": f"Bearer {carrier_token}"},
    )
    carrier_ids = [item["task_id"] for item in carrier_list.json()["data"]["items"]]
    assert task_id in carrier_ids
    assert carrier_ids.count(task_id) == 1

    receiver_detail = client.get(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {receiver_token}"},
    )
    assert receiver_detail.status_code == 200

    prechecked = client.post(
        f"/api/v1/tasks/{task_id}/precheck",
        headers=sender_headers,
        json={"passed": True, "temperature": 4.5, "seal_ok": True, "note": "预检通过"},
    )
    assert prechecked.status_code == 200
    assert prechecked.json()["data"]["status"] == "pending_handoff"
    assert prechecked.json()["data"]["precheck_passed"] is True

    cancel_after_precheck = client.post(
        f"/api/v1/tasks/{task_id}/cancel",
        headers=sender_headers,
    )
    assert cancel_after_precheck.status_code == 200
    assert cancel_after_precheck.json()["data"]["status"] == "canceled"

    deleted = client.delete(f"/api/v1/tasks/{task_id}", headers=sender_headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["task_id"] == task_id
    missing = client.get(f"/api/v1/tasks/{task_id}", headers=sender_headers)
    assert missing.status_code == 404

    demo_blocked = client.delete("/api/v1/tasks/TASK-001", headers=sender_headers)
    assert demo_blocked.status_code == 403


def test_v1_sender_can_query_assignment_candidates_with_minimal_fields():
    sender_token, _ = register_and_login("sender_directory", "sender", "高校实验室")
    _, carrier = register_and_login("carrier_directory", "carrier", "迅达冷链")
    register_and_login("receiver_directory", "receiver", "市医院检验科")
    headers = {"Authorization": f"Bearer {sender_token}"}

    response = client.get(
        "/api/v1/users?role=carrier&keyword=迅达&page=1&page_size=10",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["total"] == 1
    assert body["items"] == [
        {
            "user_id": carrier["user_id"],
            "name": "carrier_directory",
            "display_name": "carrier_directory",
            "organization": "迅达冷链",
            "role": "carrier",
            "status": "active",
        }
    ]
    assert "phone" not in body["items"][0]
    assert client.get("/api/v1/users?role=carrier").status_code == 401


def test_v1_task_rejects_direct_device_assignment():
    token, _ = register_and_login("sender_direct_device", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "禁止裸设备关联", "device_id": "CLD-DIRECT-001"},
    )
    assert created.status_code == 422
    assert created.json()["code"] == 42206

    normal_task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "缺少交接前置条件"},
    )
    task_id = normal_task.json()["data"]["task_id"]
    premature_start = client.post(
        f"/api/v1/tasks/{task_id}/start",
        headers=headers,
    )
    assert premature_start.status_code == 409
    assert premature_start.json()["code"] == 40902


def test_v1_task_identifies_assigned_device():
    response = client.get("/api/v1/tasks/TASK-001", headers=demo_admin_headers())

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
    headers = demo_admin_headers()
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
    assert (
        client.get(
            "/api/v1/tasks/TASK-001/telemetry/latest",
            headers=headers,
        ).json()["data"]
        is None
    )


def test_v1_telemetry_and_alarms_are_filtered_and_normalized():
    headers = demo_admin_headers()
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

    latest = client.get("/api/v1/tasks/TASK-001/telemetry/latest", headers=headers)
    assert latest.status_code == 200
    latest_data = latest.json()["data"]
    assert latest_data["task_id"] == "TASK-001"
    assert latest_data["box_status"] == "BOX_OPEN"
    assert latest_data["temp_status"] == "TEMP_ALERT"

    history = client.get(
        "/api/v1/tasks/TASK-001/telemetry/history?limit=10",
        headers=headers,
    )
    assert history.status_code == 200
    history_body = history.json()["data"]
    assert history_body["limit"] == 10
    assert len(history_body["items"]) == 2
    assert history_body["items"][1]["box_status"] == "BOX_CLOSED"
    assert history_body["items"][1]["temp_status"] == "TEMP_OK"

    alarms = client.get("/api/v1/tasks/TASK-001/alarms?limit=10", headers=headers)
    assert alarms.status_code == 200
    alarm_types = {item["event_type"] for item in alarms.json()["data"]["items"]}
    assert {"BOX_OPEN", "IMPACT", "SEVERE", "TEMP_ALERT"}.issubset(alarm_types)

    invalid_limit = client.get(
        "/api/v1/tasks/TASK-001/telemetry/history?limit=101",
        headers=headers,
    )
    assert invalid_limit.status_code == 422


def test_v1_handoff_state_machine_supports_sign():
    headers = demo_admin_headers()
    premature_sign = client.post("/api/v1/tasks/TASK-001/sign", headers=headers)
    assert premature_sign.status_code == 409
    assert premature_sign.json()["code"] == 40901

    started = client.post("/api/v1/tasks/TASK-001/start", headers=headers)
    assert started.status_code == 200
    assert started.json()["data"]["status"] == "in_transit"
    assert started.json()["data"]["started_at"]

    signed = client.post("/api/v1/tasks/TASK-001/sign", headers=headers)
    assert signed.status_code == 200
    assert signed.json()["data"]["status"] == "signed"
    assert signed.json()["data"]["signed_at"]

    repeated_sign = client.post("/api/v1/tasks/TASK-001/sign", headers=headers)
    assert repeated_sign.status_code == 409


def test_v1_handoff_state_machine_supports_arrive_before_sign():
    headers = demo_admin_headers()
    premature_arrive = client.post("/api/v1/tasks/TASK-001/arrive", headers=headers)
    assert premature_arrive.status_code == 409

    started = client.post("/api/v1/tasks/TASK-001/start", headers=headers)
    assert started.status_code == 200

    arrived = client.post("/api/v1/tasks/TASK-001/arrive", headers=headers)
    assert arrived.status_code == 200
    arrived_data = arrived.json()["data"]
    assert arrived_data["status"] == "arrived"
    assert arrived_data["arrived_at"]

    signed = client.post("/api/v1/tasks/TASK-001/sign", headers=headers)
    assert signed.status_code == 200
    assert signed.json()["data"]["status"] == "signed"


def test_v1_handoff_state_machine_supports_reject():
    headers = demo_admin_headers()
    assert (
        client.post("/api/v1/tasks/TASK-001/start", headers=headers).status_code == 200
    )
    rejected = client.post(
        "/api/v1/tasks/TASK-001/reject",
        headers=headers,
        json={"reason": "温度异常"},
    )
    assert rejected.status_code == 200
    rejected_data = rejected.json()["data"]
    assert rejected_data["status"] == "rejected"
    assert rejected_data["rejection_reason"] == "温度异常"
    assert rejected_data["rejected_at"]


def test_legacy_task_actions_clear_v1_rejection_state():
    headers = demo_admin_headers()
    assert (
        client.post("/api/v1/tasks/TASK-001/start", headers=headers).status_code == 200
    )
    assert (
        client.post(
            "/api/v1/tasks/TASK-001/reject",
            headers=headers,
            json={"reason": "温度异常"},
        ).status_code
        == 200
    )

    legacy_start = client.post("/api/task/start")
    assert legacy_start.status_code == 200
    assert (
        client.get(
            "/api/v1/tasks/TASK-001",
            headers=headers,
        ).json()[
            "data"
        ]["status"]
        == "in_transit"
    )

    assert (
        client.post(
            "/api/v1/tasks/TASK-001/reject",
            headers=headers,
            json={"reason": "箱体异常"},
        ).status_code
        == 200
    )
    legacy_sign = client.post("/api/task/sign")
    assert legacy_sign.status_code == 200
    assert (
        client.get(
            "/api/v1/tasks/TASK-001",
            headers=headers,
        ).json()[
            "data"
        ]["status"]
        == "signed"
    )


def test_v1_trace_report_uses_the_same_task_data():
    headers = demo_admin_headers()
    assert (
        client.post("/api/v1/tasks/TASK-001/start", headers=headers).status_code == 200
    )
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

    report = client.get("/api/v1/tasks/TASK-001/trace-report", headers=headers)
    assert report.status_code == 200
    data = report.json()["data"]
    assert data["task"]["task_id"] == "TASK-001"
    assert data["task"]["status"] == "in_transit"
    assert data["latest"]["device_id"] == "CLD-001"
    assert data["summary"]["total_records"] == 1
    assert data["summary"]["event_count"] >= 3
    assert len(data["events"]) >= 3


def test_v1_trace_report_includes_handoff_nodes():
    headers = demo_admin_headers()
    assert (
        client.post("/api/v1/tasks/TASK-001/start", headers=headers).status_code == 200
    )
    assert (
        client.post("/api/v1/tasks/TASK-001/sign", headers=headers).status_code == 200
    )

    response = client.get("/api/v1/tasks/TASK-001/trace-report", headers=headers)

    assert response.status_code == 200
    nodes = response.json()["data"]["handoff_nodes"]
    assert [node["type"] for node in nodes] == ["started", "signed"]
    assert all(node["timestamp"] for node in nodes)


def test_v1_trace_report_includes_task_status_history():
    headers = demo_admin_headers()
    assert (
        client.post("/api/v1/tasks/TASK-001/start", headers=headers).status_code == 200
    )
    assert (
        client.post("/api/v1/tasks/TASK-001/arrive", headers=headers).status_code == 200
    )
    assert (
        client.post("/api/v1/tasks/TASK-001/sign", headers=headers).status_code == 200
    )

    response = client.get("/api/v1/tasks/TASK-001/trace-report", headers=headers)

    assert response.status_code == 200
    history = response.json()["data"]["status_history"]
    statuses = [item["to_status"] for item in history]
    assert statuses[-3:] == ["in_transit", "arrived", "signed"]
    assert all(item["changed_at"] for item in history)


def test_v1_device_telemetry_accepts_official_upload_format():
    headers, secret = provision_demo_device()
    payload = {
        "device_id": "CLD-001",
        "task_id": "TASK-001",
        "sequence": 1001,
        "captured_at": "2026-07-22T18:50:00+08:00",
        "temperature": 6.2,
        "humidity": 62.5,
        "battery": 86,
        "box_status": "BOX_CLOSED",
        "move_status": "STABLE",
        "light_raw": 120,
        "location": {"lat": 30.123, "lng": 120.456, "accuracy": 20},
    }

    response = post_signed("/api/v1/device/telemetry", payload, secret, "official-001")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["saved"] == 1
    assert body["data"]["items"][0]["task_id"] == "TASK-001"
    assert body["data"]["items"][0]["battery"] == 86
    assert body["data"]["items"][0]["lat"] == 30.123

    latest = client.get("/api/v1/tasks/TASK-001/telemetry/latest", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["data"]["temperature"] == 6.2
    assert latest.json()["data"]["timestamp"] == "2026-07-22T18:50:00+08:00"


def test_v1_device_heartbeat_records_online_status():
    _, secret = provision_demo_device()
    payload = {
        "device_id": "CLD-001",
        "task_id": "TASK-001",
        "battery": 83,
        "rssi": -71,
        "network": "4G",
        "timestamp": "2026-07-22T18:51:00+08:00",
    }
    response = post_signed(
        "/api/v1/device/heartbeat",
        payload,
        secret,
        "heartbeat-001",
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["device_id"] == "CLD-001"
    assert data["task_id"] == "TASK-001"
    assert data["status"] == "online"
    assert data["battery"] == 83
    assert data["network"] == "4G"


def test_v1_devices_can_be_registered_bound_and_listed():
    sender_token, _ = register_and_login("sender_device_bind", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {sender_token}"}
    created_task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={
            "sample_name": "设备绑定测试样本",
        },
    )
    assert created_task.status_code == 200
    task_id = created_task.json()["data"]["task_id"]

    registered = client.post(
        "/api/v1/devices",
        headers=headers,
        json={
            "device_id": "CLD-BIND-001",
            "device_name": "UniKnect 一号箱",
            "model": "UniKnect Kit GEN-1 Pro",
            "device_secret": "bind-secret",
        },
    )
    assert registered.status_code == 200
    assert registered.json()["data"]["device_id"] == "CLD-BIND-001"
    assert registered.json()["data"]["status"] == "available"
    assert "device_secret" not in registered.json()["data"]
    assert "device_secret_hash" not in registered.json()["data"]

    bound = client.post(
        "/api/v1/devices/CLD-BIND-001/bind",
        headers=headers,
        json={"task_id": task_id},
    )
    assert bound.status_code == 200
    assert bound.json()["data"]["task_id"] == task_id
    assert bound.json()["data"]["status"] == "bound"

    task = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert task.status_code == 200
    assert task.json()["data"]["device_id"] == "CLD-BIND-001"

    devices = client.get("/api/v1/devices", headers=headers)
    assert devices.status_code == 200
    assert devices.json()["data"]["items"][0]["device_id"] == "CLD-BIND-001"
    assert "device_secret_hash" not in devices.json()["data"]["items"][0]

    bindings = client.get("/api/v1/devices/CLD-BIND-001/bindings", headers=headers)
    assert bindings.status_code == 200
    assert bindings.json()["data"]["items"][0]["task_id"] == task_id

    unbound = client.post("/api/v1/devices/CLD-BIND-001/unbind", headers=headers)
    assert unbound.status_code == 200
    assert unbound.json()["data"]["status"] == "available"
    assert unbound.json()["data"]["current_task_id"] is None
    assert "device_secret_hash" not in unbound.json()["data"]
    task_after_unbind = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert task_after_unbind.json()["data"]["device_id"] is None


def test_v1_device_owner_prevents_cross_sender_overwrite_and_access():
    owner_token, _ = register_and_login("sender_device_owner", "sender", "高校实验室")
    other_token, _ = register_and_login("sender_device_other", "sender", "其他实验室")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}

    assert (
        client.post(
            "/api/v1/devices",
            headers=owner_headers,
            json={"device_id": "CLD-OWNER-001", "device_secret": "owner-secret"},
        ).status_code
        == 200
    )
    overwrite = client.post(
        "/api/v1/devices",
        headers=other_headers,
        json={"device_id": "CLD-OWNER-001", "device_name": "恶意覆盖"},
    )
    assert overwrite.status_code == 409
    assert (
        client.get(
            "/api/v1/devices/CLD-OWNER-001/bindings",
            headers=other_headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/devices/CLD-OWNER-001/unbind",
            headers=other_headers,
        ).status_code
        == 404
    )


def test_v1_alarm_ack_and_resolve_flow():
    headers = demo_admin_headers()
    payload = {
        "device_id": "CLD-001",
        "task_id": "TASK-001",
        "temperature": 31.2,
        "humidity": 66.0,
        "light_raw": 22000,
        "box_status": "BOX_OPEN",
        "move_status": "STABLE",
        "temp_status": "TEMP_ALERT",
        "acc_total": 10.2,
        "motion_score": 1.2,
    }
    assert client.post("/api/device/data", json=payload).status_code == 200

    alarms = client.get("/api/v1/tasks/TASK-001/alarms", headers=headers)
    assert alarms.status_code == 200
    alarm = alarms.json()["data"]["items"][0]
    assert alarm["alarm_status"] == "new"

    acked = client.post(
        f"/api/v1/alarms/{alarm['event_id']}/ack",
        headers=headers,
    )
    assert acked.status_code == 200
    assert acked.json()["data"]["alarm_status"] == "acknowledged"
    assert acked.json()["data"]["acknowledged_at"]

    resolved = client.post(
        f"/api/v1/alarms/{alarm['event_id']}/resolve",
        headers=headers,
        json={"resolution": "已确认温度恢复正常，继续运输"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["data"]["alarm_status"] == "resolved"
    assert resolved.json()["data"]["resolution"] == "已确认温度恢复正常，继续运输"


def test_v1_handoff_session_can_confirm_responsibility_transfer():
    sender_token, _ = register_and_login("sender_handoff", "sender", "高校实验室")
    carrier_token, carrier = register_and_login(
        "carrier_handoff", "carrier", "迅达冷链"
    )
    sender_headers = {"Authorization": f"Bearer {sender_token}"}
    carrier_headers = {"Authorization": f"Bearer {carrier_token}"}

    created = client.post(
        "/api/v1/tasks",
        headers=sender_headers,
        json={"sample_name": "交接测试样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/assign",
            headers=sender_headers,
            json={"carrier_user_id": carrier["user_id"]},
        ).status_code
        == 200
    )

    handoff = client.post(
        f"/api/v1/tasks/{task_id}/handoffs",
        headers=sender_headers,
        json={"handoff_type": "sender_to_carrier", "to_user_id": carrier["user_id"]},
    )
    assert handoff.status_code == 200
    handoff_data = handoff.json()["data"]
    assert handoff_data["status"] == "pending"
    assert handoff_data["from_user_id"]
    assert handoff_data["to_user_id"] == carrier["user_id"]

    detail = client.get(
        f"/api/v1/handoffs/{handoff_data['handoff_id']}",
        headers=carrier_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["task_id"] == task_id

    missing_evidence = client.post(
        f"/api/v1/handoffs/{handoff_data['handoff_id']}/confirm",
        headers=carrier_headers,
        json={},
    )
    assert missing_evidence.status_code == 409
    assert missing_evidence.json()["code"] == 40932

    verify_handoff_qr(
        task_id,
        handoff_data["handoff_id"],
        sender_headers,
        carrier_headers,
    )
    sessions = client.get(
        f"/api/v1/tasks/{task_id}/handoffs?page=1&page_size=10",
        headers=carrier_headers,
    )
    assert sessions.status_code == 200
    session = sessions.json()["data"]["items"][0]
    assert session["handoff_id"] == handoff_data["handoff_id"]
    assert session["from_user"]["role"] == "sender"
    assert session["to_user"]["role"] == "carrier"
    assert session["evidence"]["qr_verified"] is True
    assert "token" not in sessions.text

    confirmed = client.post(
        f"/api/v1/handoffs/{handoff_data['handoff_id']}/confirm",
        headers=carrier_headers,
        json={"location": {"lat": 30.12, "lng": 120.45, "accuracy": 20}},
    )
    assert confirmed.status_code == 200
    confirmed_data = confirmed.json()["data"]
    assert confirmed_data["status"] == "confirmed"
    assert confirmed_data["task_status"] == "in_transit"
    assert confirmed_data["current_custodian"] == carrier["user_id"]
    assert confirmed_data["handoff_certificate_no"].startswith("HO-CERT-")
    assert confirmed_data["trace_hash"]

    task = client.get(f"/api/v1/tasks/{task_id}", headers=carrier_headers)
    assert task.status_code == 200
    assert task.json()["data"]["status"] == "in_transit"


def test_v1_receiver_sign_requires_confirmed_qr_handoff():
    sender_token, _ = register_and_login("sender_receive_flow", "sender", "高校实验室")
    carrier_token, carrier = register_and_login(
        "carrier_receive_flow",
        "carrier",
        "迅达冷链",
    )
    receiver_token, receiver = register_and_login(
        "receiver_receive_flow",
        "receiver",
        "市医院检验科",
    )
    sender_headers = {"Authorization": f"Bearer {sender_token}"}
    carrier_headers = {"Authorization": f"Bearer {carrier_token}"}
    receiver_headers = {"Authorization": f"Bearer {receiver_token}"}
    task = client.post(
        "/api/v1/tasks",
        headers=sender_headers,
        json={"sample_name": "接收交接证据测试"},
    ).json()["data"]
    task_id = task["task_id"]
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/assign",
            headers=sender_headers,
            json={
                "carrier_user_id": carrier["user_id"],
                "receiver_user_id": receiver["user_id"],
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/precheck",
            headers=sender_headers,
            json={"passed": True, "seal_ok": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/start",
            headers=sender_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/arrive",
            headers=carrier_headers,
        ).status_code
        == 200
    )

    premature = client.post(
        f"/api/v1/tasks/{task_id}/sign",
        headers=receiver_headers,
    )
    assert premature.status_code == 409
    assert premature.json()["code"] == 40933

    handoff = client.post(
        f"/api/v1/tasks/{task_id}/handoffs",
        headers=carrier_headers,
        json={
            "handoff_type": "carrier_to_receiver",
            "to_user_id": receiver["user_id"],
        },
    )
    assert handoff.status_code == 200
    handoff_id = handoff.json()["data"]["handoff_id"]
    verify_handoff_qr(
        task_id,
        handoff_id,
        carrier_headers,
        receiver_headers,
    )
    confirmed = client.post(
        f"/api/v1/handoffs/{handoff_id}/confirm",
        headers=receiver_headers,
        json={},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["task_status"] == "arrived"
    signed = client.post(
        f"/api/v1/tasks/{task_id}/sign",
        headers=receiver_headers,
    )
    assert signed.status_code == 200
    assert signed.json()["data"]["status"] == "signed"


def test_v1_admin_audit_logs_record_key_actions():
    admin_token, _ = register_and_login("admin_audit", "admin", "组委会")
    sender_token, _ = register_and_login("sender_audit", "sender", "高校实验室")
    _, carrier = register_and_login("carrier_audit", "carrier", "迅达冷链")
    _, receiver = register_and_login("receiver_audit", "receiver", "市医院检验科")
    headers = {"Authorization": f"Bearer {sender_token}"}

    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "审计日志测试样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/assign",
            headers=headers,
            json={
                "carrier_user_id": carrier["user_id"],
                "receiver_user_id": receiver["user_id"],
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/precheck",
            headers=headers,
            json={"passed": True, "seal_ok": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/start",
            headers=headers,
        ).status_code
        == 200
    )

    logs = client.get(
        "/api/v1/admin/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert logs.status_code == 200
    actions = [item["action"] for item in logs.json()["data"]["items"]]
    assert "auth.register" in actions
    assert "task.create" in actions
    assert "task.start" in actions

    forbidden = client.get("/api/v1/admin/audit-logs", headers=headers)
    assert forbidden.status_code == 403


def test_v1_qr_token_can_be_verified_once_and_revoked():
    sender_token, _ = register_and_login("sender_qr", "sender", "高校实验室")
    carrier_token, carrier = register_and_login("carrier_qr", "carrier", "迅达冷链")
    sender_headers = {"Authorization": f"Bearer {sender_token}"}

    created = client.post(
        "/api/v1/tasks",
        headers=sender_headers,
        json={"sample_name": "二维码测试样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/assign",
            headers=sender_headers,
            json={"carrier_user_id": carrier["user_id"]},
        ).status_code
        == 200
    )
    handoff = client.post(
        f"/api/v1/tasks/{task_id}/handoffs",
        headers=sender_headers,
        json={"handoff_type": "sender_to_carrier", "to_user_id": carrier["user_id"]},
    )
    assert handoff.status_code == 200
    handoff_id = handoff.json()["data"]["handoff_id"]

    generated = client.post(
        f"/api/v1/tasks/{task_id}/qr-tokens",
        headers=sender_headers,
        json={"action": "handoff_send", "handoff_id": handoff_id, "ttl_seconds": 60},
    )
    assert generated.status_code == 200
    qr_data = generated.json()["data"]
    assert qr_data["token"].startswith("qr_")
    assert qr_data["token_id"]
    assert qr_data["qr_payload"] == f"coldchain://handoff?token={qr_data['token']}"
    assert qr_data["handoff_id"] == handoff_id
    assert qr_data["ttl_seconds"] == 60
    assert qr_data["qr_image_data_url"].startswith("data:image/png;base64,")
    assert qr_data["refresh_after"] == 45

    verified = client.post(
        "/api/v1/qr-tokens/verify",
        headers={"Authorization": f"Bearer {carrier_token}"},
        json={"token": qr_data["token"]},
    )
    assert verified.status_code == 200
    assert verified.json()["data"]["valid"] is True
    assert verified.json()["data"]["task_id"] == task_id
    assert verified.json()["data"]["handoff_id"] == handoff_id

    repeated = client.post(
        "/api/v1/qr-tokens/verify",
        headers={"Authorization": f"Bearer {carrier_token}"},
        json={"token": qr_data["token"]},
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["valid"] is True
    assert repeated.json()["data"]["idempotent"] is True
    assert repeated.json()["data"]["handoff_id"] == handoff_id

    second = client.post(
        f"/api/v1/tasks/{task_id}/qr-tokens",
        headers=sender_headers,
        json={"action": "handoff_send", "handoff_id": handoff_id, "ttl_seconds": 60},
    )
    assert second.status_code == 200
    revoked = client.post(
        f"/api/v1/qr-tokens/{second.json()['data']['token_id']}/revoke",
        headers=sender_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == "revoked"

    revoked_verify = client.post(
        "/api/v1/qr-tokens/verify",
        headers={"Authorization": f"Bearer {carrier_token}"},
        json={"token": second.json()["data"]["token"]},
    )
    assert revoked_verify.status_code == 410


def test_v1_device_telemetry_deduplicates_device_sequence_and_updates_device_status():
    first = client.post(
        "/api/v1/device/telemetry",
        json={
            "device_id": "CLD-DEDUP-001",
            "task_id": "TASK-001",
            "sequence": 2001,
            "captured_at": "2026-07-22T20:01:00+08:00",
            "temperature": 5.1,
            "humidity": 61.2,
            "battery": 77,
            "box_status": "BOX_CLOSED",
            "move_status": "STABLE",
            "light_raw": 130,
        },
    )
    assert first.status_code == 401

    sender_token, _ = register_and_login("sender_dedup", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {sender_token}"}
    task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "去重测试样本"},
    )
    assert task.status_code == 200
    task_id = task.json()["data"]["task_id"]
    assert (
        client.post(
            "/api/v1/devices",
            headers=headers,
            json={"device_id": "CLD-DEDUP-001", "device_secret": "dedup-secret"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/devices/CLD-DEDUP-001/bind",
            headers=headers,
            json={"task_id": task_id},
        ).status_code
        == 200
    )

    payload = {
        "device_id": "CLD-DEDUP-001",
        "task_id": task_id,
        "sequence": 2001,
        "captured_at": "2026-07-22T20:01:00+08:00",
        "temperature": 5.1,
        "humidity": 61.2,
        "battery": 77,
        "box_status": "BOX_CLOSED",
        "move_status": "STABLE",
        "light_raw": 130,
    }
    saved = post_signed(
        "/api/v1/device/telemetry",
        payload,
        "dedup-secret",
        "dedup-001",
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["saved"] == 1

    duplicate = post_signed(
        "/api/v1/device/telemetry",
        payload,
        "dedup-secret",
        "dedup-002",
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["saved"] == 0
    assert duplicate.json()["data"]["duplicate"] is True

    history = client.get(
        f"/api/v1/tasks/{task_id}/telemetry/history?limit=10",
        headers=headers,
    )
    assert len(history.json()["data"]["items"]) == 1

    devices = client.get("/api/v1/devices", headers=headers)
    device = devices.json()["data"]["items"][0]
    assert device["device_id"] == "CLD-DEDUP-001"
    assert device["status"] == "online"
    assert device["battery"] == 77
    assert device["current_task_id"] == task_id


def test_v1_device_telemetry_requires_hmac_for_secret_enabled_device():
    sender_token, _ = register_and_login("sender_hmac", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {sender_token}"}
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "HMAC测试样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]
    assert (
        client.post(
            "/api/v1/devices",
            headers=headers,
            json={"device_id": "CLD-HMAC-001", "device_secret": "secret-001"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/devices/CLD-HMAC-001/bind",
            headers=headers,
            json={"task_id": task_id},
        ).status_code
        == 200
    )

    payload = {
        "device_id": "CLD-HMAC-001",
        "task_id": task_id,
        "sequence": 9001,
        "captured_at": "2026-07-22T22:01:00+08:00",
        "temperature": 5.6,
        "humidity": 60.5,
        "battery": 82,
        "box_status": "BOX_CLOSED",
        "move_status": "STABLE",
        "light_raw": 128,
    }
    timestamp = now_iso()
    nonce = "nonce-hmac-001"

    missing = client.post("/api/v1/device/telemetry", json=payload)
    assert missing.status_code == 401

    bad = client.post(
        "/api/v1/device/telemetry",
        json=payload,
        headers={
            "X-Device-Id": "CLD-HMAC-001",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": "bad-signature",
        },
    )
    assert bad.status_code == 401

    signature = sign_device_payload(payload, "secret-001", timestamp, nonce)
    good = client.post(
        "/api/v1/device/telemetry",
        json=payload,
        headers={
            "X-Device-Id": "CLD-HMAC-001",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
        },
    )
    assert good.status_code == 200
    assert good.json()["data"]["saved"] == 1

    replay = client.post(
        "/api/v1/device/telemetry",
        json={**payload, "sequence": 9002},
        headers={
            "X-Device-Id": "CLD-HMAC-001",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": sign_device_payload(
                {**payload, "sequence": 9002}, "secret-001", timestamp, nonce
            ),
        },
    )
    assert replay.status_code == 409
    assert replay.json()["code"] == 40940


def test_v1_device_secret_rotation_invalidates_old_secret_and_delivers_new_once():
    sender_token, _ = register_and_login("sender_rotate", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {sender_token}"}
    task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "密钥轮换测试"},
    ).json()["data"]
    assert (
        client.post(
            "/api/v1/devices",
            headers=headers,
            json={"device_id": "CLD-ROTATE-001", "device_secret": "old-secret"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/devices/CLD-ROTATE-001/bind",
            headers=headers,
            json={"task_id": task["task_id"]},
        ).status_code
        == 200
    )

    rotated = client.post(
        "/api/v1/devices/CLD-ROTATE-001/rotate-secret",
        headers=headers,
    )
    assert rotated.status_code == 200
    rotation_data = rotated.json()["data"]
    new_secret = rotation_data["device_secret"]
    assert new_secret != "old-secret"
    assert rotation_data["delivered_once"] is True

    listed = client.get("/api/v1/devices", headers=headers)
    assert "device_secret" not in listed.text
    assert "device_secret_hash" not in listed.text

    payload = {
        "device_id": "CLD-ROTATE-001",
        "task_id": task["task_id"],
        "sequence": 1,
        "temperature": 4.2,
        "humidity": 60.0,
        "box_status": "BOX_CLOSED",
        "move_status": "STABLE",
        "light_raw": 100,
    }
    old_key = post_signed(
        "/api/v1/device/telemetry",
        payload,
        "old-secret",
        "rotate-old",
    )
    assert old_key.status_code == 401
    new_key = post_signed(
        "/api/v1/device/telemetry",
        payload,
        new_secret,
        "rotate-new",
    )
    assert new_key.status_code == 200


def test_v1_face_profile_verify_and_manual_review_flow():
    admin_token, _ = register_and_login("admin_face", "admin", "组委会")
    sender_token, _ = register_and_login("sender_face", "sender", "高校实验室")
    carrier_token, carrier = register_and_login("carrier_face", "carrier", "迅达冷链")
    sender_headers = {"Authorization": f"Bearer {sender_token}"}
    carrier_headers = {"Authorization": f"Bearer {carrier_token}"}

    enrolled = client.post(
        "/api/v1/face/enroll",
        headers=carrier_headers,
        json={
            "template_id": "tpl_carrier_face",
            "consent": True,
            "quality_score": 0.92,
        },
    )
    assert enrolled.status_code == 200
    assert enrolled.json()["data"]["status"] == "active"
    assert enrolled.json()["data"]["consent_at"]

    profile = client.get("/api/v1/face/profile", headers=carrier_headers)
    assert profile.status_code == 200
    assert profile.json()["data"]["has_profile"] is True
    assert profile.json()["data"]["template_id"] == "tpl_carrier_face"

    created = client.post(
        "/api/v1/tasks",
        headers=sender_headers,
        json={"sample_name": "人脸核验测试样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/assign",
            headers=sender_headers,
            json={"carrier_user_id": carrier["user_id"]},
        ).status_code
        == 200
    )
    handoff = client.post(
        f"/api/v1/tasks/{task_id}/handoffs",
        headers=sender_headers,
        json={"handoff_type": "sender_to_carrier", "to_user_id": carrier["user_id"]},
    )
    assert handoff.status_code == 200
    handoff_id = handoff.json()["data"]["handoff_id"]
    verify_handoff_qr(
        task_id,
        handoff_id,
        sender_headers,
        carrier_headers,
    )

    failed_verify = client.post(
        "/api/v1/face/verify",
        headers=carrier_headers,
        json={
            "handoff_id": handoff_id,
            "qr_token": "qr_demo",
            "capture_file_id": "FILE_demo",
            "liveness_token": "live_demo",
            "liveness_passed": True,
            "similarity_score": 0.62,
            "location": {"lat": 30.12, "lng": 120.45, "accuracy": 20},
        },
    )
    assert failed_verify.status_code == 200
    verify_data = failed_verify.json()["data"]
    assert verify_data["verified"] is False
    assert verify_data["manual_review_required"] is True
    assert verify_data["verification_id"].startswith("FV-")
    assert "qr_token" not in verify_data
    assert "liveness_token" not in verify_data

    reviews = client.get(
        "/api/v1/admin/face-reviews",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reviews.status_code == 200
    review = reviews.json()["data"]["items"][0]
    assert review["verification_id"] == verify_data["verification_id"]
    assert review["status"] == "pending_review"

    approved = client.post(
        f"/api/v1/admin/face-reviews/{review['id']}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "approved"

    deleted = client.delete("/api/v1/face/profile", headers=carrier_headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True


def test_v1_admin_can_list_users_tasks_and_update_user_status():
    admin_token, _ = register_and_login("admin_manage", "admin", "组委会")
    sender_token, sender = register_and_login("sender_manage", "sender", "高校实验室")
    task = client.post(
        "/api/v1/tasks",
        headers={"Authorization": f"Bearer {sender_token}"},
        json={"sample_name": "管理员任务查询样本"},
    )
    assert task.status_code == 200
    task_id = task.json()["data"]["task_id"]

    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    users = client.get("/api/v1/admin/users", headers=admin_headers)
    assert users.status_code == 200
    usernames = [item["username"] for item in users.json()["data"]["items"]]
    assert "sender_manage" in usernames

    tasks = client.get("/api/v1/admin/tasks", headers=admin_headers)
    assert tasks.status_code == 200
    assert tasks.json()["data"]["page"] == 1
    assert tasks.json()["data"]["page_size"] == 20
    assert tasks.json()["data"]["total"] >= 2
    task_ids = [item["task_id"] for item in tasks.json()["data"]["items"]]
    assert task_id in task_ids

    forbidden = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {sender_token}"},
    )
    assert forbidden.status_code == 403

    updated = client.patch(
        f"/api/v1/admin/users/{sender['user_id']}/status",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["status"] == "disabled"

    disabled_login = client.post(
        "/api/v1/auth/login",
        json={"phone": "sender_manage", "password": "password123"},
    )
    assert disabled_login.status_code == 403

    disabled_token = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {sender_token}"},
    )
    assert disabled_token.status_code == 401


def test_v1_files_evidence_is_recorded_and_included_in_trace_report():
    sender_token, _ = register_and_login("sender_file", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {sender_token}"}
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "证据文件测试样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]

    uploaded = client.post(
        "/api/v1/files",
        headers=headers,
        json={
            "task_id": task_id,
            "file_name": "precheck-photo.jpg",
            "file_type": "image/jpeg",
            "file_size": 12345,
            "sha256": "a" * 64,
            "usage": "precheck",
            "related_type": "task",
            "related_id": task_id,
            "storage_url": "local://evidence/precheck-photo.jpg",
        },
    )
    assert uploaded.status_code == 200
    file_data = uploaded.json()["data"]
    assert file_data["file_id"].startswith("FILE-")
    assert file_data["task_id"] == task_id
    assert file_data["sha256"] == "a" * 64

    file_detail = client.get(f"/api/v1/files/{file_data['file_id']}", headers=headers)
    assert file_detail.status_code == 200
    assert (
        file_detail.json()["data"]["download_url"]
        == "local://evidence/precheck-photo.jpg"
    )
    assert file_detail.json()["data"]["expires_in"] == 300

    report = client.get(f"/api/v1/tasks/{task_id}/trace-report", headers=headers)
    assert report.status_code == 200
    report_data = report.json()["data"]
    assert report_data["summary"]["evidence_count"] == 1
    assert report_data["evidence_files"][0]["file_id"] == file_data["file_id"]
    assert report_data["trace_hash"]
    assert report_data["report_version"] == "MVP-1"


def test_v1_real_evidence_upload_download_and_authorization():
    sender_token, _ = register_and_login("sender_upload", "sender", "高校实验室")
    other_token, _ = register_and_login("sender_upload_other", "sender", "其他实验室")
    headers = {"Authorization": f"Bearer {sender_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}
    task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "真实文件上传测试"},
    ).json()["data"]
    png_content = b"\x89PNG\r\n\x1a\n" + b"cold-chain-evidence"
    digest = hashlib.sha256(png_content).hexdigest()

    uploaded = client.post(
        "/api/v1/files/upload",
        headers=headers,
        data={
            "task_id": task["task_id"],
            "usage": "handoff_evidence",
            "expected_sha256": digest,
        },
        files={"file": ("handoff-proof.png", png_content, "image/png")},
    )
    assert uploaded.status_code == 200
    item = uploaded.json()["data"]
    assert item["sha256"] == digest
    assert item["file_size"] == len(png_content)
    assert item["download_url"].endswith("/download")
    assert "storage_path" not in uploaded.text

    duplicate = client.post(
        "/api/v1/files/upload",
        headers=headers,
        data={"task_id": task["task_id"], "usage": "handoff_evidence"},
        files={"file": ("same-proof.png", png_content, "image/png")},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["message"] == "file already exists"
    assert duplicate.json()["data"]["file_id"] == item["file_id"]

    downloaded = client.get(item["download_url"], headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == png_content
    assert downloaded.headers["content-type"] == "image/png"
    assert client.get(item["download_url"], headers=other_headers).status_code == 404

    invalid = client.post(
        "/api/v1/files/upload",
        headers=headers,
        data={"task_id": task["task_id"], "usage": "handoff_evidence"},
        files={"file": ("fake.png", b"not-a-png", "image/png")},
    )
    assert invalid.status_code == 415
    assert invalid.json()["code"] == 41502


def test_v1_notifications_are_created_for_task_alarm_and_can_be_read():
    sender_token, _ = register_and_login("sender_notify", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {sender_token}"}
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "通知测试样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]

    assert (
        client.post(
            "/api/v1/devices",
            headers=headers,
            json={"device_id": "CLD-NOTIFY-001", "device_secret": "notify-secret"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/devices/CLD-NOTIFY-001/bind",
            headers=headers,
            json={"task_id": task_id},
        ).status_code
        == 200
    )
    payload = {
        "device_id": "CLD-NOTIFY-001",
        "task_id": task_id,
        "sequence": 3001,
        "captured_at": "2026-07-22T21:01:00+08:00",
        "temperature": 31.2,
        "humidity": 66.0,
        "battery": 76,
        "box_status": "BOX_OPEN",
        "move_status": "STABLE",
        "temp_status": "TEMP_ALERT",
        "light_raw": 22000,
    }
    alert = post_signed(
        "/api/v1/device/telemetry",
        payload,
        "notify-secret",
        "notify-001",
    )
    assert alert.status_code == 200

    notifications = client.get("/api/v1/notifications", headers=headers)
    assert notifications.status_code == 200
    item = notifications.json()["data"]["items"][0]
    assert item["task_id"] == task_id
    assert item["category"] == "alarm"
    assert item["is_read"] is False
    messages = [entry["message"] for entry in notifications.json()["data"]["items"]]
    assert any("温度异常" in message or "开箱" in message for message in messages)

    read = client.post(f"/api/v1/notifications/{item['id']}/read", headers=headers)
    assert read.status_code == 200
    assert read.json()["data"]["is_read"] is True
    assert read.json()["data"]["read_at"]


def test_v1_trace_report_pdf_returns_downloadable_pdf():
    headers = demo_admin_headers()
    assert (
        client.post("/api/v1/tasks/TASK-001/start", headers=headers).status_code == 200
    )
    response = client.get("/api/v1/tasks/TASK-001/trace-report.pdf", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert b"TASK-001" in response.content


def test_v1_task_status_actions_support_idempotency_key():
    headers = demo_admin_headers()
    first = client.post(
        "/api/v1/tasks/TASK-001/start",
        headers={**headers, "Idempotency-Key": "start-once-001"},
    )
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data["status"] == "in_transit"

    repeated = client.post(
        "/api/v1/tasks/TASK-001/start",
        headers={**headers, "Idempotency-Key": "start-once-001"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["status"] == "in_transit"
    assert repeated.json()["data"]["started_at"] == first_data["started_at"]

    sign = client.post(
        "/api/v1/tasks/TASK-001/sign",
        headers={**headers, "Idempotency-Key": "sign-once-001"},
    )
    assert sign.status_code == 200
    signed_at = sign.json()["data"]["signed_at"]

    repeated_sign = client.post(
        "/api/v1/tasks/TASK-001/sign",
        headers={**headers, "Idempotency-Key": "sign-once-001"},
    )
    assert repeated_sign.status_code == 200
    assert repeated_sign.json()["data"]["status"] == "signed"
    assert repeated_sign.json()["data"]["signed_at"] == signed_at


def test_v1_telemetry_history_supports_time_cursor_and_downsample_filters():
    headers, secret = provision_demo_device()
    rows = [
        ("2026-07-22T10:00:00+08:00", 4.1, 1),
        ("2026-07-22T10:01:00+08:00", 4.2, 2),
        ("2026-07-22T10:02:00+08:00", 4.3, 3),
        ("2026-07-22T10:03:00+08:00", 4.4, 4),
    ]
    for captured_at, temperature, sequence in rows:
        payload = {
            "device_id": "CLD-001",
            "task_id": "TASK-001",
            "sequence": sequence,
            "captured_at": captured_at,
            "temperature": temperature,
            "humidity": 60.0,
            "battery": 90,
            "box_status": "BOX_CLOSED",
            "move_status": "STABLE",
            "light_raw": 100,
        }
        response = post_signed(
            "/api/v1/device/telemetry",
            payload,
            secret,
            f"history-{sequence}",
        )
        assert response.status_code == 200

    filtered = client.get(
        "/api/v1/tasks/TASK-001/telemetry/history"
        "?start_time=2026-07-22T10:01:00+08:00"
        "&end_time=2026-07-22T10:03:00+08:00"
        "&limit=10",
        headers=headers,
    )
    assert filtered.status_code == 200
    filtered_data = filtered.json()["data"]
    assert [item["sequence"] for item in filtered_data["items"]] == [4, 3, 2]
    assert filtered_data["next_cursor"] == 2

    cursor_page = client.get(
        "/api/v1/tasks/TASK-001/telemetry/history?cursor=3&limit=10",
        headers=headers,
    )
    assert cursor_page.status_code == 200
    assert [item["sequence"] for item in cursor_page.json()["data"]["items"]] == [2, 1]

    downsampled = client.get(
        "/api/v1/tasks/TASK-001/telemetry/history?limit=10&downsample=2",
        headers=headers,
    )
    assert downsampled.status_code == 200
    assert [item["sequence"] for item in downsampled.json()["data"]["items"]] == [4, 2]


def test_v1_device_low_battery_and_offline_generate_alarms():
    headers, secret = provision_demo_device()
    payload = {
        "device_id": "CLD-001",
        "task_id": "TASK-001",
        "sequence": 5001,
        "captured_at": "2026-07-22T10:10:00+08:00",
        "temperature": 4.2,
        "humidity": 60.0,
        "battery": 15,
        "box_status": "BOX_CLOSED",
        "move_status": "STABLE",
        "light_raw": 100,
    }
    response = post_signed(
        "/api/v1/device/telemetry",
        payload,
        secret,
        "low-battery-001",
    )
    assert response.status_code == 200

    alarms = client.get("/api/v1/tasks/TASK-001/alarms?limit=10", headers=headers)
    assert alarms.status_code == 200
    alarm_types = [item["event_type"] for item in alarms.json()["data"]["items"]]
    assert "LOW_BATTERY" in alarm_types

    with closing(sqlite3.connect(DATABASE_PATH)) as conn, conn:
        conn.execute(
            """
            UPDATE devices
            SET last_seen_at = ?, status = ?
            WHERE device_id = ?
            """,
            ("2026-07-22T08:00:00+08:00", "online", "CLD-001"),
        )

    assert main_module.scan_offline_devices() == 1
    with closing(sqlite3.connect(DATABASE_PATH)) as conn, conn:
        proactive_alarm = conn.execute(
            """
            SELECT id FROM event_log
            WHERE task_id = ? AND event_type = ?
            """,
            ("TASK-001", "DEVICE_OFFLINE"),
        ).fetchone()
    assert proactive_alarm

    devices = client.get("/api/v1/devices")
    assert devices.status_code == 401

    devices = client.get("/api/v1/devices", headers=headers)
    assert devices.status_code == 200
    device = devices.json()["data"]["items"][0]
    assert device["device_id"] == "CLD-001"
    assert device["status"] == "offline"

    offline_alarms = client.get(
        "/api/v1/tasks/TASK-001/alarms?limit=10",
        headers=headers,
    )
    assert offline_alarms.status_code == 200
    offline_types = [
        item["event_type"] for item in offline_alarms.json()["data"]["items"]
    ]
    assert "DEVICE_OFFLINE" in offline_types

    summary = client.get("/api/v1/dashboard/summary", headers=headers)
    assert summary.status_code == 200
    summary_data = summary.json()["data"]
    assert summary_data["offline_devices"] == 1
    assert summary_data["abnormal_tasks"] >= 1
    assert summary_data["today_alarm_count"] >= 2
    assert "pending_pack" in summary_data["status_distribution"]
    assert summary_data["alarm_distribution"]["DEVICE_OFFLINE"] == 1


def test_openapi_and_all_frontend_mocks_are_parseable_and_current():
    paths = app.openapi()["paths"]
    expected_paths = {
        "/api/v1/users",
        "/api/v1/tasks/{task_id}/handoffs",
        "/api/v1/files/upload",
        "/api/v1/files/{file_id}/download",
        "/api/v1/dashboard/summary",
        "/api/v1/devices/{device_id}/rotate-secret",
    }
    assert expected_paths.issubset(paths)

    mock_dir = Path(__file__).resolve().parent.parent / "docs" / "api" / "mock"
    mock_files = list(mock_dir.glob("*.json"))
    assert len(mock_files) >= 14
    for mock_file in mock_files:
        body = json.loads(mock_file.read_text(encoding="utf-8"))
        assert {"code", "message", "data"}.issubset(body)


def test_legacy_device_data_remaps_to_bound_waybill():
    """硬件仍报 TASK-001 时，应按设备当前绑定运单落库。"""
    token, _ = register_and_login("sender_remap_task", "sender", "高校实验室")
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"sample_name": "运单归属改写样本"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]
    assert task_id != "TASK-001"

    assert (
        client.post(
            "/api/v1/devices",
            headers=headers,
            json={"device_id": "CLD-REMAP-001", "device_secret": "remap-secret"},
        ).status_code
        == 200
    )
    bound = client.post(
        f"/api/v1/devices/CLD-REMAP-001/bind",
        headers=headers,
        json={"task_id": task_id},
    )
    assert bound.status_code == 200

    payload = {
        "device_id": "CLD-REMAP-001",
        "task_id": "TASK-001",
        "temperature": 4.2,
        "humidity": 55.0,
        "light_raw": 120,
        "box_status": "BOX_CLOSED",
        "move_status": "STABLE",
        "temp_status": "TEMP_OK",
        "acc_total": 9.8,
        "motion_score": 0.1,
    }
    response = client.post("/api/device/data", json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["task_id"] == task_id

    report = client.get(f"/api/v1/tasks/{task_id}/trace-report", headers=headers)
    assert report.status_code == 200
    summary = report.json()["data"]["summary"]
    assert summary["total_records"] >= 1
    assert summary["avg_temperature"] == 4.2
