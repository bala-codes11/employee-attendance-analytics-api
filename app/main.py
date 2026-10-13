"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def startup():
    # Verify MongoDB connectivity
    client.admin.command("ping")

    # Prevent duplicate employee codes
    db.employees.create_index(
    [("emp_code", ASCENDING)],
    unique=True,
    name="uq_employee_code",
)

    # One attendance record per employee per date
    db.attendance_logs.create_index(
    [("emp_code", ASCENDING), ("date", ASCENDING)],
    unique=True,
    name="uq_attendance_employee_date",
)

    # Support attendance date filtering and sorting
    db.attendance_logs.create_index(
    [("date", DESCENDING), ("emp_code", ASCENDING)],
    name="idx_attendance_date_employee",
)
# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))

    start = punch_in.replace(
        hour=h, minute=m, second=0, microsecond=0
    )

    minutes = int((punch_in - start).total_seconds() // 60)

    return minutes if (punch_in - start).total_seconds() > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(
        str((punch_out - punch_in).total_seconds())
    )
    hours = seconds / Decimal("3600")

    # R4: Round to 2 decimal places using half-up rounding
    rounded = hours.quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return float(rounded)



def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
    shift_start: str = "09:30",
) -> int:
    end_date = date.fromisoformat(date_str)
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    is_overnight = (end_h, end_m) <= (start_h, start_m)

    end = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        end_h,
        end_m,
        tzinfo=IST,
    )

    if is_overnight:
        end += timedelta(days=1)

    overtime_minutes = int(
        (punch_out - end).total_seconds() // 60
    )

    return overtime_minutes if overtime_minutes >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
    max_length=120,
    pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
)
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")

        if parsed.isoformat() != value:
            raise ValueError("joined_on must use YYYY-MM-DD format")

        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )

class AttendanceRegularizationIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )
    punch_out: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )
    reason: str = Field(min_length=1, max_length=500)
    by: str = Field(min_length=1, max_length=100)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable"
        )


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()

    created_at = datetime.now(timezone.utc)
    doc["created_at"] = created_at

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    # Remove MongoDB's internal ID before returning the response.
    doc.pop("_id", None)

    # Return creation time in epoch milliseconds.
    doc["created_at"] = int(created_at.timestamp() * 1000)

    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}

    # Apply department filter only when a non-empty value is provided.
    if department and department.strip():
        query["department"] = department.strip()

    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    # Convert MongoDB datetime to epoch milliseconds.
    for item in items:
        created_at = item.get("created_at")

        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            item["created_at"] = int(created_at.timestamp() * 1000)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.status not in ("PRESENT", "WFH", "ON_DUTY"):
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    # Convert epoch milliseconds to IST, or use current time.
    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=IST,
        )
    else:
        ts = datetime.now(IST)

    # Truncate to whole seconds.
    ts = ts.replace(microsecond=0)

    start_h, start_m = map(
        int, emp["shift_start"].split(":")
    )
    end_h, end_m = map(
        int, emp["shift_end"].split(":")
    )

    is_overnight = (end_h, end_m) <= (start_h, start_m)

    # Determine the attendance date.
    attendance_day = ts.date()

    if is_overnight and (ts.hour, ts.minute) < (end_h, end_m):
        attendance_day -= timedelta(days=1)

    attendance_date = attendance_day.isoformat()

    # Calculate lateness relative to the shift start
    # on the attendance date.
    shift_start_dt = datetime(
        attendance_day.year,
        attendance_day.month,
        attendance_day.day,
        start_h,
        start_m,
        tzinfo=IST,
    )

    elapsed_seconds = (ts - shift_start_dt).total_seconds()

    late_minutes = (
    int(elapsed_seconds // 60)
    if elapsed_seconds > 600
    else 0
)

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this attendance date",
        )

    # Return epoch milliseconds; never expose MongoDB _id.
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=IST,
        )
    else:
        ts = datetime.now(IST)

    # Truncate to whole seconds.
    ts = ts.replace(microsecond=0)

    # Find the latest punch-in not later than punch-out.
    # PyMongo converts aware query datetimes to BSON UTC.


    
    
   
    record = db.attendance_logs.find_one(
    {
        "emp_code": body.emp_code,
        "punch_out": None,
        "punch_in": {"$lte": ts.astimezone(timezone.utc)},
    },
    sort=[("punch_in", DESCENDING)],
)

    if record is None:
     raise HTTPException(
        status_code=404,
        detail="No open punch-in found before the requested punch-out",
    )

    punch_in_time = record["punch_in"]

    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    punch_in_time = punch_in_time.astimezone(IST)

    if ts <= punch_in_time:
        raise HTTPException(
        status_code=422,
        detail="Punch-out must be after punch-in",
    )

    if (ts - punch_in_time).total_seconds() > 24 * 3600:
        raise HTTPException(
        status_code=422,
        detail="Punch-out cannot be more than 24 hours after punch-in",
    )


    work_hours = compute_work_hours(
        punch_in_time,
        ts,
    )

    overtime = compute_overtime(
        ts,
        emp["shift_end"],
        record["date"],
        emp["shift_start"],
    )

    # R5: Use the rounded work_hours result.
    half_day = work_hours < 4.50

    # Atomic update prevents concurrent punch-outs
    # from both succeeding.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day,
            },
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Already punched out",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    # Convert stored BSON datetimes to epoch milliseconds.
   
    # Convert audit history timestamps to epoch milliseconds.
  
    # Convert stored BSON datetimes to epoch milliseconds.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)

        if value is not None and isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)

            updated[field] = int(value.timestamp()) * 1000

    # Convert audit history timestamps to epoch milliseconds.
    for entry in updated.get("history", []):
        value = entry.get("at")

        if value is not None and isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)

            entry["at"] = int(value.timestamp()) * 1000

    return updated




@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    # Validate attendance status.
    allowed_statuses = {
        "PRESENT",
        "ABSENT",
        "LEAVE",
        "WFH",
        "ON_DUTY",
    }

    if status is not None and status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    # Build the MongoDB query.
    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    # Validate and apply date filters.
    if date_from is not None:
        try:
            parsed_from = date.fromisoformat(date_from)
            if parsed_from.isoformat() != date_from:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="date_from must use YYYY-MM-DD format",
            )

    if date_to is not None:
        try:
            parsed_to = date.fromisoformat(date_to)
            if parsed_to.isoformat() != date_to:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="date_to must use YYYY-MM-DD format",
            )

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to",
        )

    if date_from or date_to:
        date_filter = {}

        if date_from:
            date_filter["$gte"] = date_from

        if date_to:
            date_filter["$lte"] = date_to

        query["date"] = date_filter

    if status is not None:
        query["status"] = status

    # Count all matching records before pagination.
    total = db.attendance_logs.count_documents(query)

    # Fetch the requested page.
    items = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    # Convert BSON datetimes to epoch milliseconds.
    
    # Convert BSON datetimes to epoch milliseconds.
    for item in items:
        # Convert punch timestamps.
        for field in ("punch_in", "punch_out"):
            value = item.get(field)

            if value is not None and isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)

                item[field] = int(value.timestamp()) * 1000

        # Convert audit history timestamps.
        for entry in item.get("history", []):
            value = entry.get("at")

            if value is not None and isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)

                entry["at"] = int(value.timestamp()) * 1000

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{attendance_date}")
def regularize_attendance(
    emp_code: str,
    attendance_date: str,
    body: AttendanceRegularizationIn,
):
    # 1. Validate attendance date.
    try:
        parsed_date = date.fromisoformat(attendance_date)
        if parsed_date.isoformat() != attendance_date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Date must use YYYY-MM-DD format",
        )

    # 2. Check employee and attendance record.
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": attendance_date,
    })
    if record is None:
        raise HTTPException(404, "Attendance record not found")

    fields_set = body.model_fields_set

    # 3. Validate status.
    allowed_statuses = {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }

    if body.status is not None and body.status not in allowed_statuses:
        raise HTTPException(422, "Invalid attendance status")

    new_status = (
        body.status if body.status is not None else record["status"]
    )

    # ABSENT and LEAVE cannot be supplied with punch times.
    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in fields_set or "punch_out" in fields_set:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch times",
            )

    # 4. Convert supplied epoch milliseconds to whole-second IST datetimes.
  
    # 4. Normalize stored BSON datetimes and supplied timestamps.
    # MongoDB may return naive datetimes representing UTC.
    def normalize_stored_datetime(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST)

    new_punch_in = normalize_stored_datetime(record.get("punch_in"))
    new_punch_out = normalize_stored_datetime(record.get("punch_out"))

    if "punch_in" in fields_set:
        new_punch_in = (
            datetime.fromtimestamp(body.punch_in // 1000, tz=IST)
            if body.punch_in is not None
            else None
        )

    if "punch_out" in fields_set:
        new_punch_out = (
            datetime.fromtimestamp(body.punch_out // 1000, tz=IST)
            if body.punch_out is not None
            else None
        )


    # ABSENT and LEAVE clear both punch times.
    if new_status in ("ABSENT", "LEAVE"):
        new_punch_in = None
        new_punch_out = None

    # 5. Validate punch-in requirements.
    if new_status in ("PRESENT", "WFH", "ON_DUTY"):
        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in",
            )

    if new_punch_in is not None:
        if new_punch_in.date().isoformat() != attendance_date:
            raise HTTPException(
                status_code=422,
                detail="Punch-in must be on the attendance date",
            )

    # 6. Validate punch-out.
    if new_punch_out is not None:
        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Punch-out requires punch-in",
            )

        if new_punch_out <= new_punch_in:
            raise HTTPException(
                status_code=422,
                detail="Punch-out must be after punch-in",
            )

        if (new_punch_out - new_punch_in).total_seconds() > 86400:
            raise HTTPException(
                status_code=422,
                detail="Punch-out cannot be more than 24 hours after punch-in",
            )

    # 7. Normalize stored BSON datetimes to UTC.
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    if old_in is not None and old_in.tzinfo is None:
        old_in = old_in.replace(tzinfo=timezone.utc)

    if old_out is not None and old_out.tzinfo is None:
        old_out = old_out.replace(tzinfo=timezone.utc)

    new_in_utc = (
        new_punch_in.astimezone(timezone.utc)
        if new_punch_in is not None else None
    )
    new_out_utc = (
        new_punch_out.astimezone(timezone.utc)
        if new_punch_out is not None else None
    )

    changes = {}
    updates = {}

    # 8. Record only actual changes.
    if new_status != record["status"]:
        changes["status"] = new_status
        updates["status"] = new_status

    if new_in_utc != old_in:
        changes["punch_in"] = (
            int(new_in_utc.timestamp()) * 1000
            if new_in_utc is not None else None
        )
        updates["punch_in"] = new_in_utc

    if new_out_utc != old_out:
        changes["punch_out"] = (
            int(new_out_utc.timestamp()) * 1000
            if new_out_utc is not None else None
        )
        updates["punch_out"] = new_out_utc

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="No changes to apply",
        )

    # 9. Recalculate derived fields.
    if new_status in ("ABSENT", "LEAVE"):
        updates.update({
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        })

    elif new_punch_in is not None:
        shift_start = datetime.strptime(
            emp["shift_start"], "%H:%M"
        ).time()

        start_dt = datetime.combine(
            parsed_date, shift_start, tzinfo=IST
        )

        elapsed_seconds = (
            new_punch_in - start_dt
        ).total_seconds()

        updates["late_minutes"] = (
            int(elapsed_seconds // 60)
            if elapsed_seconds > 600
            else 0
        )

        if new_punch_out is not None:
            work_hours = compute_work_hours(
                new_punch_in, new_punch_out
            )

            updates["work_hours"] = work_hours
            updates["overtime_minutes"] = compute_overtime(
                new_punch_out,
                emp["shift_end"],
                attendance_date,
                emp["shift_start"],
            )
            updates["half_day"] = work_hours < 4.50
        else:
            updates["work_hours"] = None
            updates["overtime_minutes"] = 0
            updates["half_day"] = False

    # 10. Append audit history.
    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.by,
        "reason": body.reason.strip(),
        "changes": changes,
    }

    # 11. Update the record.
    
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": record["status"],
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
        },
        {
            "$set": updates,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently",
        )


    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently",
        )

    # 12. Return the updated record without MongoDB _id.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)

        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)

            updated[field] = int(value.timestamp()) * 1000

    for entry in updated.get("history", []):
        at = entry.get("at")

        if at is not None:
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)

            entry["at"] = int(at.timestamp()) * 1000

    return updated

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    # Validate employee.
    emp = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0},
    )

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Determine the month boundaries.
    try:
        month_start = date.fromisoformat(month + "-01")
    except ValueError:
        raise HTTPException(422, "Invalid month")

    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    else:
        next_month = date(
            month_start.year,
            month_start.month + 1,
            1,
        )

    month_end = next_month - timedelta(days=1)

    pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$set": {
                "joined_date": {
                    "$dateFromString": {
                        "dateString": "$joined_on",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$set": {
                "period_start": {
                    "$max": [
                        "$joined_date",
                        {
                            "$dateFromString": {
                                "dateString": month_start.isoformat(),
                                "format": "%Y-%m-%d",
                            }
                        },
                    ]
                },
                "period_end": {
                    "$dateFromString": {
                        "dateString": month_end.isoformat(),
                        "format": "%Y-%m-%d",
                    }
                },
            }
        },
        {
            "$set": {
                "working_days": {
                    "$cond": [
                        {"$gt": ["$period_start", "$period_end"]},
                        0,
                        {
                            "$size": {
                                "$filter": {
                                    "input": {
                                        "$map": {
                                            "input": {
                                                "$range": [
                                                    0,
                                                    {
                                                        "$add": [
                                                            {
                                                                "$dateDiff": {
                                                                    "startDate": "$period_start",
                                                                    "endDate": "$period_end",
                                                                    "unit": "day",
                                                                }
                                                            },
                                                            1,
                                                        ]
                                                    },
                                                ]
                                            },
                                            "as": "offset",
                                            "in": {
                                                "$dateAdd": {
                                                    "startDate": "$period_start",
                                                    "unit": "day",
                                                    "amount": "$$offset",
                                                }
                                            },
                                        }
                                    },
                                    "as": "day",
                                    "cond": {
                                        "$and": [
                                            {
                                                "$ne": [
                                                    {"$dayOfWeek": "$$day"},
                                                    1,
                                                ]
                                            },
                                            {
                                                "$ne": [
                                                    {"$dayOfWeek": "$$day"},
                                                    7,
                                                ]
                                            },
                                        ]
                                    },
                                }
                            }
                        },
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "code": "$emp_code",
                    "start": month_start.isoformat(),
                    "end": next_month.isoformat(),
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", "$$start"]},
                                    {"$lt": ["$date", "$$end"]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$set": {
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$and": [
                                            {
                                                "$in": [
                                                    "$$log.status",
                                                    ["PRESENT", "WFH", "ON_DUTY"],
                                                ]
                                            },
                                            {
                                                "$in": [
                                                    {
                                                        "$dayOfWeek": {
                                                            "$dateFromString": {
                                                                "dateString": "$$log.date",
                                                                "format": "%Y-%m-%d",
                                                            }
                                                        }
                                                    },
                                                    [2, 3, 4, 5, 6],
                                                ]
                                            },
                                        ]
                                    },
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                        }
                    }
                },
                "leave_days": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "LEAVE"]},
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$gt": ["$$log.late_minutes", 0]},
                        }
                    }
                },
                
"total_late_minutes": {
    "$sum": {
        "$map": {
            "input": "$logs",
            "as": "log",
            "in": {
                "$cond": [
                    {"$gt": ["$$log.late_minutes", 0]},
                    "$$log.late_minutes",
                    0,
                ]
            },
        }
    }
},
"total_overtime_minutes": {
    "$sum": {
        "$map": {
            "input": "$logs",
            "as": "log",
            "in": {
                "$cond": [
                    {"$gt": ["$$log.overtime_minutes", 0]},
                    "$$log.overtime_minutes",
                    0,
                ]
            },
        }
    }
},

            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": 1,
                "month": {"$literal": month},
                "working_days": 1,
                "present_days": 1,
               "leave_days": 1,
"late_count": 1,
"total_late_minutes": 1,
"total_overtime_minutes": 1,
            }
        },
    ]

    result = list(db.employees.aggregate(pipeline))

    if not result:
        raise HTTPException(404, "Employee not found")

    
    data = result[0]

    present = Decimal(str(data.get("present_days", 0)))
    working = int(data.get("working_days", 0))

    data["attendance_pct"] = (
            float(
                (present / Decimal(working) * Decimal("100"))
                .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            )
            if working > 0
            else None
        )

    return data


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
):
    # Calculate the month's date boundaries.
    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    # Start from employees so employees without logs
    # are included in headcount.
    employee_filter = {
        "joined_on": {"$lte": month_end.isoformat()}
    }

    if department:
        employee_filter["department"] = department

    pipeline = [
        {"$match": employee_filter},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {"$gte": ["$date", month_start.isoformat()]},
                                    {"$lt": ["$date", next_month.isoformat()]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$set": {
                "present_days_employee": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$and": [
                                            {
                                                "$in": [
                                                    "$$log.status",
                                                    ["PRESENT", "WFH", "ON_DUTY"],
                                                ]
                                            },
                                            {
                                                "$in": [
                                                    {
                                                        "$dayOfWeek": {
                                                            "$dateFromString": {
                                                                "dateString": "$$log.date",
                                                                "format": "%Y-%m-%d",
                                                            }
                                                        }
                                                    },
                                                    [2, 3, 4, 5, 6],
                                                ]
                                            },
                                        ]
                                    },
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                        }
                    }
                },
                "valid_hours": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$logs",
                                "as": "log",
                                "cond": {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$$log.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {"$ne": ["$$log.work_hours", None]},
                                    ]
                                },
                            }
                        },
                        "as": "log",
                        "in": "$$log.work_hours",
                    }
                },
                "late_count_employee": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$gt": ["$$log.late_minutes", 0]},
                        }
                    }
                },
                "late_minutes_employee": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$gt": ["$$log.late_minutes", 0]},
                                    "$$log.late_minutes",
                                    0,
                                ]
                            },
                        }
                    }
                },
                "leave_count_employee": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "LEAVE"]},
                        }
                    }
                },
                "on_duty_count_employee": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "ON_DUTY"]},
                        }
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days_employee"},
                "late_count": {"$sum": "$late_count_employee"},
                "total_late_minutes": {"$sum": "$late_minutes_employee"},
                "leave_count": {"$sum": "$leave_count_employee"},
                "on_duty_count": {"$sum": "$on_duty_count_employee"},
                "all_hours": {"$push": "$valid_hours"},
            }
        },
        {
            "$set": {
                "flattened_hours": {
                    "$reduce": {
                        "input": "$all_hours",
                        "initialValue": [],
                        "in": {"$concatArrays": ["$$value", "$$this"]},
                    }
                }
            }
        },
        
        {
            "$set": {
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": [{"$size": "$flattened_hours"}, 0]},
                        {
                            "$divide": [
                                {
                                    "$reduce": {
                                        "input": "$flattened_hours",
                                        "initialValue": 0,
                                        "in": {"$add": ["$$value", "$$this"]},
                                    }
                                },
                                {"$size": "$flattened_hours"},
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    rows = list(db.employees.aggregate(pipeline))

    # Apply required half-up rounding to reported numbers.
    for row in rows:
        row["present_days"] = float(
            Decimal(str(row["present_days"])).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )

        if row["avg_work_hours"] is not None:
            row["avg_work_hours"] = float(
                Decimal(str(row["avg_work_hours"])).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )

        for field in (
            "headcount",
            "late_count",
            "total_late_minutes",
            "leave_count",
            "on_duty_count",
        ):
            row[field] = int(row[field])

    return {"month": month, "items": rows}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    # Calculate the month boundaries.
    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    pipeline = [
        # Select attendance records for the requested month.
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        # Get employee name and department.
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    # Optional department filter.
    if department:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        # Calculate totals for each employee.
        {
            "$group": {
                "_id": "$emp_code",
                "name": {"$first": "$employee.name"},
                "department": {"$first": "$employee.department"},
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        # Rank using total late minutes. Ties share the same rank.
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        # Apply the limit to ranks, not array positions.
        {"$match": {"rank": {"$lte": limit}}},
        # Stable ordering for employees with tied totals.
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": 1,
                "department": 1,
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    # Validate dates and range.
    try:
        start_day = date.fromisoformat(from_date)
        end_day = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format",
        )

    if start_day.isoformat() != from_date or end_day.isoformat() != to_date:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format",
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    day_count = (end_day - start_day).days + 1
    if day_count > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    # Check that the department exists.
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    start_dt = datetime.combine(start_day, datetime.min.time())
    end_exclusive_dt = datetime.combine(
        end_day + timedelta(days=1),
        datetime.min.time(),
    )

    pipeline = [
        # Seed the pipeline so dates are returned even with no logs.
        {
            "$documents": [
                {
                    "date": start_dt,
                    "end_date": end_exclusive_dt,
                }
            ]
        },
        # Generate every date in the requested range.
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_exclusive_dt],
                },
            }
        },
        {
            "$match": {
                "date": {
                    "$gte": start_dt,
                    "$lt": end_exclusive_dt,
                }
            }
        },
        {
            "$set": {
                "day": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                        "timezone": "UTC",
                    }
                },
                "weekday": {
                    "$dayOfWeek": "$date"
                },
            }
        },
        # Employees belonging to this department and already joined.
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$day"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$department",
                                            department,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$joined_on",
                                            "$$day",
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {"$project": {"_id": 0, "emp_code": 1}},
                ],
                "as": "employees",
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$employees"},
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$weekday", 2]},
                        {"$lte": ["$weekday", 6]},
                    ]
                },
                "employee_codes": "$employees.emp_code",
            }
        },
        # Attendance logs for this date and department.
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "day": "$day",
                    "codes": "$employee_codes",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$day"]},
                                    {
                                        "$in": [
                                            "$emp_code",
                                            "$$codes",
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$set": {
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    {"$ifNull": ["$$log.late_minutes", 0]},
                                    0,
                                ]
                            },
                        }
                    }
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$present_count",
                                        "$headcount",
                                    ]
                                },
                                4,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        # Moving average over the current day and up to six previous days.
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        },
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$day",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": [{"$type": "$moving_avg_7d"}, "missing"]},
                        None,
                        {"$round": ["$moving_avg_7d", 4]},
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.aggregate(pipeline))

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": items,
    }




@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    attendance_date: Optional[str] = Query(default=None, alias="date"),
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    limit: int = Query(default=10, ge=1, le=50),
):
    params = {
        "emp_code": emp_code,
        "month": month,
        "department": department,
        "from": from_date,
        "to": to_date,
        "date": attendance_date,
        "status": status,
        "page": page,
        "page_size": page_size,
        "limit": limit,
    }

    # Validate YYYY-MM and construct the month boundaries.
    # The function parameter is named attendance_date so it does not
    # shadow datetime.date.
    def parse_month(value):
        if not isinstance(value, str):
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        try:
            parts = value.split("-")
            if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
                raise ValueError

            year = int(parts[0])
            month_num = int(parts[1])

            if not 1 <= month_num <= 12:
                raise ValueError

            month_start = date(year, month_num, 1)

        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="month must be a valid YYYY-MM value",
            )

        next_month = (
            date(year + 1, 1, 1)
            if month_num == 12
            else date(year, month_num + 1, 1)
        )

        return month_start, next_month

    try:
        # 1. Attendance list
        if endpoint == "attendance_list":
            match = {}

                    
            if params.get("emp_code"):
                match["emp_code"] = params["emp_code"]

            if params.get("status"):
                match["status"] = params["status"]

            if params.get("date"):
                match["date"] = params["date"]
            else:
                date_range = {}
                if params.get("from"):
                    date_range["$gte"] = params["from"]
                if params.get("to"):
                    date_range["$lte"] = params["to"]
                if date_range:
                    match["date"] = date_range


            pipeline = [
                {"$match": match},
                {"$sort": {"date": -1, "emp_code": 1}},
                {"$skip": (page - 1) * page_size},
                {"$limit": page_size},
            ]

            collection = "attendance_logs"

        # 2. Employee monthly summary
       
        elif endpoint == "employee_monthly":
            emp_code = params.get("emp_code")
            month = params.get("month")

            if not emp_code or not month:
                raise HTTPException(
                    status_code=422,
                    detail="emp_code and month are required",
                )

            month_start, next_month = parse_month(month)

            start_date = month_start.isoformat()
            end_date = next_month.isoformat()

            pipeline = [
                {
                    "$match": {
                        "emp_code": emp_code,
                    }
                },
                {
                    "$lookup": {
                        "from": "attendance_logs",
                        "let": {"code": "$emp_code"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {
                                                "$eq": [
                                                    "$emp_code",
                                                    "$$code",
                                                ]
                                            },
                                            {
                                                "$gte": [
                                                    "$date",
                                                    start_date,
                                                ]
                                            },
                                            {
                                                "$lt": [
                                                    "$date",
                                                    end_date,
                                                ]
                                            },
                                        ]
                                    }
                                }
                            },
                            {
                                "$project": {
                                    "_id": 0,
                                    "emp_code": 1,
                                    "date": 1,
                                    "status": 1,
                                    "punch_in": 1,
                                    "punch_out": 1,
                                    "work_hours": 1,
                                    "late_minutes": 1,
                                    "overtime_minutes": 1,
                                    "half_day": 1,
                                }
                            },
                        ],
                        "as": "logs",
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "emp_code": 1,
                        "logs": 1,
                    }
                },
            ]

            collection = "employees"


        # 3. Department summary
        
       
        elif endpoint == "department_summary":
            month = params.get("month")

            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required",
                )

            month_start, next_month = parse_month(month)
            month_end = next_month - timedelta(days=1)

            employee_filter = {
                "joined_on": {"$lte": month_end.isoformat()}
            }

            if params.get("department"):
                employee_filter["department"] = params["department"]

            pipeline = [
                {"$match": employee_filter},
                {
                    "$lookup": {
                        "from": "attendance_logs",
                        "let": {"employee_code": "$emp_code"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {
                                                "$eq": [
                                                    "$emp_code",
                                                    "$$employee_code",
                                                ]
                                            },
                                            {
                                                "$gte": [
                                                    "$date",
                                                    month_start.isoformat(),
                                                ]
                                            },
                                            {
                                                "$lt": [
                                                    "$date",
                                                    next_month.isoformat(),
                                                ]
                                            },
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "logs",
                    }
                },
                {
                    "$set": {
                        "present_days_employee": {
                            "$sum": {
                                "$map": {
                                    "input": {
                                        "$filter": {
                                            "input": "$logs",
                                            "as": "log",
                                            "cond": {
                                                "$and": [
                                                    {
                                                        "$in": [
                                                            "$$log.status",
                                                            ["PRESENT", "WFH", "ON_DUTY"],
                                                        ]
                                                    },
                                                    {
                                                        "$in": [
                                                            {
                                                                "$dayOfWeek": {
                                                                    "$dateFromString": {
                                                                        "dateString": "$$log.date",
                                                                        "format": "%Y-%m-%d",
                                                                    }
                                                                }
                                                            },
                                                            [2, 3, 4, 5, 6],
                                                        ]
                                                    },
                                                ]
                                            },
                                        }
                                    },
                                    "as": "log",
                                    "in": {
                                        "$cond": [
                                            {"$eq": ["$$log.half_day", True]},
                                            0.5,
                                            1,
                                        ]
                                    },
                                }
                            }
                        },
                        "valid_hours": {
                            "$map": {
                                "input": {
                                    "$filter": {
                                        "input": "$logs",
                                        "as": "log",
                                        "cond": {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$$log.status",
                                                        ["PRESENT", "WFH", "ON_DUTY"],
                                                    ]
                                                },
                                                {"$ne": ["$$log.work_hours", None]},
                                            ]
                                        },
                                    }
                                },
                                "as": "log",
                                "in": "$$log.work_hours",
                            }
                        },
                        "late_count_employee": {
                            "$size": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {"$gt": ["$$log.late_minutes", 0]},
                                }
                            }
                        },
                        "late_minutes_employee": {
                            "$sum": {
                                "$map": {
                                    "input": "$logs",
                                    "as": "log",
                                    "in": {
                                        "$cond": [
                                            {"$gt": ["$$log.late_minutes", 0]},
                                            "$$log.late_minutes",
                                            0,
                                        ]
                                    },
                                }
                            }
                        },
                        "leave_count_employee": {
                            "$size": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {"$eq": ["$$log.status", "LEAVE"]},
                                }
                            }
                        },
                        "on_duty_count_employee": {
                            "$size": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {"$eq": ["$$log.status", "ON_DUTY"]},
                                }
                            }
                        },
                    }
                },
                {
                    "$group": {
                        "_id": "$department",
                        "headcount": {"$sum": 1},
                        "present_days": {"$sum": "$present_days_employee"},
                        "late_count": {"$sum": "$late_count_employee"},
                        "total_late_minutes": {"$sum": "$late_minutes_employee"},
                        "leave_count": {"$sum": "$leave_count_employee"},
                        "on_duty_count": {"$sum": "$on_duty_count_employee"},
                        "all_hours": {"$push": "$valid_hours"},
                    }
                },
                {
                    "$set": {
                        "flattened_hours": {
                            "$reduce": {
                                "input": "$all_hours",
                                "initialValue": [],
                                "in": {"$concatArrays": ["$$value", "$$this"]},
                            }
                        }
                    }
                },
                {
                    "$set": {
                        "avg_work_hours": {
                            "$cond": [
                                {"$gt": [{"$size": "$flattened_hours"}, 0]},
                                {
                                    "$divide": [
                                        {
                                            "$reduce": {
                                                "input": "$flattened_hours",
                                                "initialValue": 0,
                                                "in": {"$add": ["$$value", "$$this"]},
                                            }
                                        },
                                        {"$size": "$flattened_hours"},
                                    ]
                                },
                                None,
                            ]
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "department": "$_id",
                        "headcount": 1,
                        "present_days": 1,
                        "avg_work_hours": 1,
                        "late_count": 1,
                        "total_late_minutes": 1,
                        "leave_count": 1,
                        "on_duty_count": 1,
                    }
                },
                {"$sort": {"department": 1}},
            ]

            collection = "employees"


        # 4. Late leaderboard
        elif endpoint == "late_leaderboard":
            month = params.get("month")

            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required",
                )

            month_start, next_month = parse_month(month)

            pipeline = [
                {
                    "$match": {
                        "date": {
                            "$gte": month_start.isoformat(),
                            "$lt": next_month.isoformat(),
                        },
                        "late_minutes": {"$gt": 0},
                    }
                },
                {
                    "$lookup": {
                        "from": "employees",
                        "localField": "emp_code",
                        "foreignField": "emp_code",
                        "as": "employee",
                    }
                },
                {"$unwind": "$employee"},
            ]

            if params.get("department"):
                pipeline.append({
                    "$match": {
                        "employee.department": params["department"]
                    }
                })

            pipeline.extend([
                {
                    "$group": {
                        "_id": "$emp_code",
                        "name": {"$first": "$employee.name"},
                        "department": {"$first": "$employee.department"},
                        "total_late_minutes": {
                            "$sum": "$late_minutes"
                        },
                        "late_count": {"$sum": 1},
                    }
                },
                {
                    "$setWindowFields": {
                        "sortBy": {"total_late_minutes": -1},
                        "output": {
                            "rank": {"$rank": {}}
                        },
                    }
                },
                {"$match": {"rank": {"$lte": limit}}},
                {
                    "$sort": {
                        "total_late_minutes": -1,
                        "_id": 1,
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "rank": 1,
                        "emp_code": "$_id",
                        "name": 1,
                        "department": 1,
                        "total_late_minutes": 1,
                        "late_count": 1,
                    }
                },
            ])

            collection = "attendance_logs"

        # 5. Department trend
        
        # 5. Department trend
        elif endpoint == "department_trend":
            department = params.get("department")
            from_date = params.get("from")
            to_date = params.get("to")

            if not department or not from_date or not to_date:
                raise HTTPException(
                    status_code=422,
                    detail="department, from and to are required",
                )

            try:
                start_day = date.fromisoformat(from_date)
                end_day = date.fromisoformat(to_date)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=422,
                    detail="Dates must use YYYY-MM-DD format",
                )

            if (
                start_day.isoformat() != from_date
                or end_day.isoformat() != to_date
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Dates must use YYYY-MM-DD format",
                )

            day_count = (end_day - start_day).days + 1

            if end_day < start_day or day_count > 92:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid date range; maximum 92 days",
                )

            if db.employees.count_documents(
                {"department": department}
            ) == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Department not found",
                )

            start_dt = datetime.combine(
                start_day, datetime.min.time()
            )
            end_exclusive_dt = datetime.combine(
                end_day + timedelta(days=1),
                datetime.min.time(),
            )

            pipeline = [
                {
                    "$documents": [
                        {
                            "date": start_dt,
                            "end_date": end_exclusive_dt,
                        }
                    ]
                },
                {
                    "$densify": {
                        "field": "date",
                        "range": {
                            "step": 1,
                            "unit": "day",
                            "bounds": [start_dt, end_exclusive_dt],
                        },
                    }
                },
                {
                    "$match": {
                        "date": {
                            "$gte": start_dt,
                            "$lt": end_exclusive_dt,
                        }
                    }
                },
                {
                    "$set": {
                        "day": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$date",
                                "timezone": "UTC",
                            }
                        },
                        "weekday": {"$dayOfWeek": "$date"},
                    }
                },
                {
                    "$lookup": {
                        "from": "employees",
                        "let": {"day": "$day"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {
                                                "$eq": [
                                                    "$department",
                                                    department,
                                                ]
                                            },
                                            {
                                                "$lte": [
                                                    "$joined_on",
                                                    "$$day",
                                                ]
                                            },
                                        ]
                                    }
                                }
                            },
                            {
                                "$project": {
                                    "_id": 0,
                                    "emp_code": 1,
                                }
                            },
                        ],
                        "as": "employees",
                    }
                },
                {
                    "$set": {
                        "headcount": {"$size": "$employees"},
                        "is_working_day": {
                            "$and": [
                                {"$gte": ["$weekday", 2]},
                                {"$lte": ["$weekday", 6]},
                            ]
                        },
                        "employee_codes": "$employees.emp_code",
                    }
                },
                {
                    "$lookup": {
                        "from": "attendance_logs",
                        "let": {
                            "day": "$day",
                            "codes": "$employee_codes",
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {
                                                "$eq": [
                                                    "$date",
                                                    "$$day",
                                                ]
                                            },
                                            {
                                                "$in": [
                                                    "$emp_code",
                                                    "$$codes",
                                                ]
                                            },
                                        ]
                                    }
                                }
                            },
                            {
                                "$project": {
                                    "_id": 0,
                                    "status": 1,
                                    "half_day": 1,
                                    "late_minutes": 1,
                                }
                            },
                        ],
                        "as": "logs",
                    }
                },
                {
                    "$set": {
                        "present_count": {
                            "$sum": {
                                "$map": {
                                    "input": {
                                        "$filter": {
                                            "input": "$logs",
                                            "as": "log",
                                            "cond": {
                                                "$in": [
                                                    "$$log.status",
                                                    [
                                                        "PRESENT",
                                                        "WFH",
                                                        "ON_DUTY",
                                                    ],
                                                ]
                                            },
                                        }
                                    },
                                    "as": "log",
                                    "in": {
                                        "$cond": [
                                            {
                                                "$eq": [
                                                    "$$log.half_day",
                                                    True,
                                                ]
                                            },
                                            0.5,
                                            1,
                                        ]
                                    },
                                }
                            }
                        },
                        "late_count": {
                            "$size": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$gt": [
                                            {
                                                "$ifNull": [
                                                    "$$log.late_minutes",
                                                    0,
                                                ]
                                            },
                                            0,
                                        ]
                                    },
                                }
                            }
                        },
                    }
                },
                {
                    "$set": {
                        "attendance_rate": {
                            "$cond": [
                                {
                                    "$and": [
                                        "$is_working_day",
                                        {"$gt": ["$headcount", 0]},
                                    ]
                                },
                                {
                                    "$round": [
                                        {
                                            "$divide": [
                                                "$present_count",
                                                "$headcount",
                                            ]
                                        },
                                        4,
                                    ]
                                },
                                None,
                            ]
                        }
                    }
                },
                {
                    "$setWindowFields": {
                        "sortBy": {"date": 1},
                        "output": {
                            "moving_avg_7d": {
                                "$avg": "$attendance_rate",
                                "window": {
                                    "documents": [-6, 0]
                                },
                            }
                        },
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "date": "$day",
                        "is_working_day": 1,
                        "headcount": 1,
                        "present_count": 1,
                        "late_count": 1,
                        "attendance_rate": 1,
                        "moving_avg_7d": {
                            "$cond": [
                                {
                                    "$eq": [
                                        {"$type": "$moving_avg_7d"},
                                        "missing",
                                    ]
                                },
                                None,
                                {"$round": ["$moving_avg_7d", 4]},
                            ]
                        },
                    }
                },
                {"$sort": {"date": 1}},
            ]

            collection = None


        else:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Invalid endpoint. Use: attendance_list, "
                    "employee_monthly, department_summary, "
                    "late_leaderboard, department_trend"
                ),
            )
        
        if collection is None:
            explain_command = {
                "aggregate": 1,
                "pipeline": pipeline,
                "cursor": {},
            }
        else:
            explain_command = {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            }

        result = db.command(
            "explain",
            explain_command,
            verbosity="executionStats",
        )

        return result

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Explain failed: {str(exc)}",
        )
