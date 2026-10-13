# REVIEW.md

List of defects found in the starter `app/main.py`.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_work_hours` | Python's built-in `round()` uses ties-to-even rounding, not the required half-up rounding. | Test a duration whose hours fall exactly halfway between two hundredths. | Used `Decimal` with `ROUND_HALF_UP`. |
| 2 | `compute_overtime` | Overtime is counted even when it is less than 30 minutes. | Punch out 15 minutes after shift end; the starter reports overtime instead of zero. | Return overtime only when the whole-minute total is at least 30. |
| 3 | `compute_overtime` | The shift-end calculation does not account for overnight shifts. | Test a shift ending after midnight. | Calculate the shift end on the following date when the shift crosses midnight. |
| 4 | `EmployeeIn` | Employee fields lack contract validation for employee code, email, names, department, shift times and joining date. | Submit malformed employee data or identical shift start/end times. | Added Pydantic field constraints and date/shift validators. |
| 5 | `PunchInIn` | The punch timestamp and status are not adequately validated. | Send an invalid timestamp or an unsupported status. | Added strict timestamp bounds and explicit allowed-status checks. |
| 6 | `create_employee` | Checking for an existing employee before inserting is not safe under concurrent requests. | Send two simultaneous requests with the same employee code. | Added a unique index on `employees.emp_code` and handle duplicate-key errors as HTTP 409. |
| 7 | `create_employee` | The creation timestamp is naive and the response does not follow the epoch-millisecond contract. | Inspect `created_at` in the response and compare it with the API contract. | Store a UTC timestamp and return epoch milliseconds. |
| 8 | `list_employees` | The skip calculation starts at `page * page_size`, skipping the first page of records. | Request page 1 and compare it with the first sorted records. | Changed the offset to `(page - 1) * page_size`. |
| 9 | `list_employees` | The total count ignores the department filter. | Request employees from one department and compare `total` with the filtered results. | Count using the same query as the list. |
| 10 | `list_employees` | Pagination has no bounds validation and results are not consistently sorted. | Try page 0, a negative page size, or multiple pages. | Added page/page-size constraints and stable employee-code sorting. |
| 11 | `punch_in` | A missing employee is not handled before accessing employee shift details. | Punch in using an unknown employee code. | Return HTTP 404 when the employee does not exist. |
| 12 | `punch_in` | The duplicate-record check followed by an insert is vulnerable to a race condition. | Send concurrent punch-in requests for the same employee and date. | Enforced a unique compound index on `(emp_code, date)` and mapped duplicate-key errors to HTTP 409. |
| 13 | `punch_in` | Timestamp handling does not consistently use IST or validate the timestamp range. | Send invalid, out-of-range, or boundary timestamps. | Validate timestamps, convert them to IST and truncate to whole seconds. |
| 14 | `punch_in` | The response exposes an extra attendance `id`, contrary to the response contract. | Inspect the response JSON. | Removed the internal identifier from the API response. |
| 15 | `list_attendance` | The endpoint fetches all matching records and sorts/paginates in Python. This is inefficient at scale. | Test with a large attendance collection and inspect the query plan. | Applied sorting, skip and limit in MongoDB and added supporting indexes. |
| 16 | `list_attendance` | The query does not exclude MongoDB's internal `_id`, and records are returned with an extra `id`. | Inspect the response JSON. | Projected out `_id` and returned only contract fields. |
| 17 | `list_attendance` | Date filters, pagination and status values are not validated against the contract. | Submit malformed dates, invalid status values or out-of-range pagination. | Added date/status validation and page/page-size constraints. |
| 18 | Remaining endpoints | Punch-out, regularization, analytics and explain endpoints are missing from the starter. | Call the endpoints listed in the OpenAPI contract. | Implemented the missing endpoints and their required validation and aggregation pipelines. |

## Things examined and considered

- The 10-minute grace period is strict: lateness starts only after more than 10 minutes.
- The attendance list should sort by date descending and employee code ascending, not by date alone.
- Analytics must run through MongoDB aggregation pipelines rather than fetching all records and calculating results in Python.
- Duplicate checks in application code are not a replacement for unique indexes when concurrent requests are possible.