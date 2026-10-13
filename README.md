# Employee Attendance & Analytics API

A REST API built with **FastAPI, Python, and MongoDB** to manage employee attendance, record punch-in and punch-out times, regularize attendance records, and generate attendance analytics.

## Features

- **Employee Management:** Create employees and list them with department filtering and pagination.
- **Attendance Tracking:** Record punch-in and punch-out times, calculate late minutes, work hours, overtime, and half-day status.
- **Attendance Regularization:** Update attendance records with validation and an audit history.
- **Employee Monthly Summary:** Calculate monthly attendance, leave, lateness, overtime, and attendance percentage.
- **Department Analytics:** Summarize department headcount, attendance, work hours, and late records.
- **Late Leaderboard:** Rank employees by total late minutes, including ties.
- **Department Trend:** Generate daily department attendance trends, including dates without attendance records.
- **Query Explain:** Inspect MongoDB execution plans for supported list and analytics queries.
- **Validation and Indexing:** Validate API inputs and use MongoDB indexes to support queries and prevent duplicate records.

## Tech Stack

- Python 3.11+
- FastAPI
- PyMongo
- MongoDB 6.0+
- Pytest

## Setup and Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/bala-codes11/employee-attendance-analytics-api.git
   cd employee-attendance-analytics-api
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   ```

   Windows PowerShell:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   macOS/Linux:

   ```bash
   source .venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Configure MongoDB by creating a local `.env` file in the project root:

   ```env
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=attendance_db
   ```

   Make sure MongoDB is running before starting the application.

5. Start the API:

   ```bash
   uvicorn app.main:app --port 8000
   ```

6. Open the interactive API documentation:

   http://127.0.0.1:8000/docs

## Sample Data

To load the provided sample data into your configured database, run:

```bash
python sample_seed.py
```

Run this only when you intend to seed the configured database.

## Running Tests

Run the automated API tests with:

```bash
python -m pytest -v test_api.py
```

The current local test run completed with **18 passed**.

## Project Structure

```text
├── app/
│   └── main.py
├── sample_data/
├── sample_seed.py
├── test_api.py
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md
```

## Configuration and Security

MongoDB connection settings are read from environment variables. Keep your `.env` file and virtual environment out of version control.

## API Documentation

The API provides interactive Swagger documentation at `/docs` when the application is running.