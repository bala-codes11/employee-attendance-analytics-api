# DECISIONS.md

**1. Indexes:** I created a unique index on `employees.emp_code` to prevent duplicate employee codes, a unique compound index on `attendance_logs.emp_code` and `date` to allow only one attendance record per employee per day, and an index on `attendance_logs.date` descending and `emp_code` ascending to support attendance filtering and sorting. I would consider an additional department and joining-date index if query plans show it is needed.

**2. Punch-in race:** If two identical punch-in requests arrive together, both might pass the initial duplicate check. The unique compound index allows only one insert to succeed. The other raises a duplicate-key error, which the API should return as HTTP 409.

**3. Leaderboard ties:** The leaderboard uses MongoDB `$rank`, then filters ranks up to the requested limit. Employees tied at the cutoff are all returned because they share the same rank.

**4. Headcount:** The department summary starts from employees who joined on or before the month’s end, then looks up their attendance logs. Employees with no logs are still included in headcount.

**5. Scaling:** For 100 times more data, I would inspect execution plans and index usage on a realistic dataset, optimize the aggregation pipelines, and consider pagination or precomputed summaries for expensive analytics.