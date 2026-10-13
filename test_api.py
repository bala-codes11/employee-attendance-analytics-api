"""
Regression tests for the HROne FastAPI + MongoDB attendance API.

Safety: tests use a separate MongoDB database named attendance_test_db.
Run from project root:
  .\\.venv\\Scripts\\python.exe -m pip install pytest httpx
  .\\.venv\\Scripts\\python.exe -m pytest -v tests\\test_api.py
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

# Set this BEFORE importing app.main (the database is selected at import time).
os.environ["MONGO_DB"] = "attendance_test_db"

import pytest
from fastapi.testclient import TestClient
from app import main as api

IST = timezone(timedelta(hours=5, minutes=30))


def epoch_ms(year, month, day, hour, minute):
    return int(datetime(year, month, day, hour, minute, tzinfo=IST).timestamp() * 1000)

def employee_payload(department="Automation QA"):
    suffix = str(int(uuid.uuid4().hex[:8], 16) % 1_000_000).zfill(6)

    return {
        "emp_code": f"EMP{suffix}",
        "name": "Automation Test Employee",
        "email": f"test-{suffix}@example.com",
        "department": department,
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }
@pytest.fixture()
def client():
    if api.db.name != "attendance_test_db":
        raise RuntimeError(f"Refusing to test database {api.db.name!r}")
    with TestClient(api.app) as c:
        api.db.employees.delete_many({})
        api.db.attendance_logs.delete_many({})
        yield c
        api.db.employees.delete_many({})
        api.db.attendance_logs.delete_many({})


def create_employee(client, department="Automation QA"):
    data = employee_payload(department)
    response = client.post("/employees", json=data)
    assert response.status_code == 201, response.text
    return data


def create_completed_attendance(client):
    employee = create_employee(client)
    response = client.post("/attendance/punch-in", json={
        "emp_code": employee["emp_code"],
        "punched_at": epoch_ms(2026, 10, 20, 9, 45),
    })
    assert response.status_code == 201, response.text
    assert response.json()["late_minutes"] == 15
    response = client.post("/attendance/punch-out", json={
        "emp_code": employee["emp_code"],
        "punched_at": epoch_ms(2026, 10, 20, 19, 0),
    })
    assert response.status_code == 200, response.text
    return employee, response.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_employee_create_duplicate_and_id_hidden(client):
    data = employee_payload()
    first = client.post("/employees", json=data)
    second = client.post("/employees", json=data)
    assert first.status_code == 201, first.text
    assert "_id" not in first.json()
    assert isinstance(first.json()["created_at"], int)
    assert second.status_code == 409


def test_invalid_employee_returns_422(client):
    data = employee_payload()
    data["email"] = "bad-email"
    assert client.post("/employees", json=data).status_code == 422


def test_employee_filter_and_pagination(client):
    create_employee(client, "QA")
    create_employee(client, "QA")
    create_employee(client, "Engineering")
    r = client.get("/employees", params={"department": "QA", "page": 1, "page_size": 1})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    assert len(r.json()["items"]) == 1


def test_punch_in_lateness_and_duplicate_guard(client):
    employee = create_employee(client)
    payload = {"emp_code": employee["emp_code"], "punched_at": epoch_ms(2026, 10, 20, 9, 45)}
    first = client.post("/attendance/punch-in", json=payload)
    second = client.post("/attendance/punch-in", json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["date"] == "2026-10-20"
    assert first.json()["late_minutes"] == 15
    assert first.json()["punch_out"] is None
    assert second.status_code == 409


def test_punch_out_work_hours_overtime(client):
    _, record = create_completed_attendance(client)
    assert record["work_hours"] == 9.25
    assert record["overtime_minutes"] == 30
    assert record["half_day"] is False
    assert isinstance(record["punch_in"], int)
    assert isinstance(record["punch_out"], int)


def test_punch_out_before_punch_in_is_rejected(client):
    employee = create_employee(client)
    client.post("/attendance/punch-in", json={
        "emp_code": employee["emp_code"],
        "punched_at": epoch_ms(2026, 10, 20, 9, 45),
    })
    r = client.post("/attendance/punch-out", json={
        "emp_code": employee["emp_code"],
        "punched_at": epoch_ms(2026, 10, 20, 9, 44),
    })
    assert r.status_code == 404, r.text


def test_attendance_list_filters(client):
    employee, _ = create_completed_attendance(client)
    r = client.get("/attendance", params={
        "emp_code": employee["emp_code"],
        "date_from": "2026-10-20",
        "date_to": "2026-10-20",
    })
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["emp_code"] == employee["emp_code"]


def test_regularization_audit_and_noop_rejected(client):
    employee, _ = create_completed_attendance(client)
    url = f"/attendance/{employee['emp_code']}/2026-10-20"
    r = client.patch(url, json={
        "status": "WFH",
        "reason": "Approved remote work",
        "by": "automated-test",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "WFH"
    assert len(body["history"]) == 1
    assert set(body["history"][0]["changes"]) == {"status"}
    assert "_id" not in body

    noop = client.patch(url, json={
        "status": "WFH",
        "reason": "No change requested",
        "by": "automated-test",
    })
    assert noop.status_code == 422, noop.text


def test_monthly_summary(client):
    employee, _ = create_completed_attendance(client)
    r = client.get(f"/analytics/employees/{employee['emp_code']}/monthly",
                   params={"month": "2026-10"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["working_days"] == 22
    assert body["present_days"] == 1
    assert body["late_count"] == 1
    assert body["total_late_minutes"] == 15
    assert body["total_overtime_minutes"] == 30
    assert body["attendance_pct"] == pytest.approx(4.5455)


def test_department_summary_and_late_leaderboard(client):
    employee, _ = create_completed_attendance(client)
    r = client.get("/analytics/departments/summary",
                   params={"month": "2026-10", "department": "Automation QA"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["headcount"] == 1
    assert items[0]["present_days"] == 1
    assert items[0]["total_late_minutes"] == 15

    r = client.get("/analytics/leaderboard/late",
                   params={"month": "2026-10", "department": "Automation QA", "limit": 10})
    assert r.status_code == 200, r.text
    leaders = r.json()["items"]
    assert len(leaders) == 1
    assert leaders[0]["emp_code"] == employee["emp_code"]
    assert leaders[0]["rank"] == 1


def test_department_trend_gap_filling(client):
    create_completed_attendance(client)
    r = client.get("/analytics/departments/Automation%20QA/trend",
                   params={"from": "2026-10-20", "to": "2026-10-22"})
    assert r.status_code == 200, r.text
    rows = r.json()["items"]
    assert [x["date"] for x in rows] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    assert all(x["headcount"] == 1 for x in rows)
    assert rows[0]["present_count"] == 1
    assert rows[0]["attendance_rate"] == 1.0
    assert rows[1]["attendance_rate"] == 0.0


def test_trend_validation(client):
    create_employee(client)
    unknown = client.get("/analytics/departments/NoSuchDept/trend",
                         params={"from": "2026-10-20", "to": "2026-10-22"})
    assert unknown.status_code == 404
    too_long = client.get("/analytics/departments/Automation%20QA/trend",
                          params={"from": "2026-01-01", "to": "2026-04-03"})
    assert too_long.status_code == 422


@pytest.mark.parametrize("endpoint,params", [
    ("attendance_list", {"date_from": "2026-10-20", "date_to": "2026-10-20"}),
    ("employee_monthly", {"emp_code": "EMP900001", "month": "2026-10"}),
    ("department_summary", {"month": "2026-10"}),
    ("late_leaderboard", {"month": "2026-10", "limit": 10}),
    ("department_trend", {"department": "Automation QA",
                          "from": "2026-10-20", "to": "2026-10-22"}),
])
def test_explain_endpoints(endpoint, params, client):
    create_employee(client)
    r = client.get(f"/admin/explain/{endpoint}", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") == 1, body
    assert body.get("explainVersion") == "1", body

