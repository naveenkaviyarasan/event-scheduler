# Event Scheduling & Resource Allocation System
**Aerele Technologies – Flask Hiring Test Assignment v2**

---

## 🐛 Login Bug Fix (Root Cause)

Flask-Login's `UserMixin` defines `is_active` as a **property** internally.  
The original code had:
```python
is_active = db.Column(db.Boolean, default=True)   # ❌ BREAKS login_user()
```
The SQLAlchemy column descriptor overwrote Flask-Login's property, so
`login_user()` silently rejected every session — the page just reloaded.

**Fix applied:**
```python
active = db.Column(db.Boolean, default=True)   # ✅ renamed DB column

@property
def is_active(self):          # ✅ proper Flask-Login hook
    return bool(self.active)
```

---

## ✅ Features

| Feature | Status |
|---|---|
| Event CRUD (create / view / edit / soft-delete) | ✅ |
| Resource CRUD (room / instructor / equipment) | ✅ |
| Resource allocation workflow | ✅ |
| Conflict detection with explanations | ✅ |
| Capacity rules (room vs attendees) | ✅ |
| Equipment quantity constraints | ✅ |
| Utilisation report with date-range filter | ✅ |
| CSV export | ✅ |
| Weekly calendar view | ✅ |
| Authentication + Roles (Admin / Organizer / Viewer) | ✅ |
| REST API (events, allocations, resources, conflict check) | ✅ |
| Unit tests – 8 conflict scenarios | ✅ |
| Audit log | ✅ |
| Soft delete | ✅ |
| Docker setup | ✅ |

---

## 🚀 Setup – Step by Step

### Option A: Local (Python venv)

**Step 1 – Clone / unzip the project**
```
event_scheduler/
├── app.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md
└── tests/
    ├── __init__.py
    └── test_conflicts.py
```

**Step 2 – Create & activate virtual environment**
```bash
# Windows (PowerShell)
python -m venv venv
venv\Scripts\activate

# Mac / Linux
python3 -m venv venv
source venv/bin/activate
```

**Step 3 – Install dependencies**
```bash
pip install -r requirements.txt
```

**Step 4 – Run the app**
```bash
python app.py
```

You should see:
```
✅  Seeded: admin/admin123  organizer/org123  viewer/view123
 * Running on http://127.0.0.1:5000
```

**Step 5 – Open in browser**
```
http://localhost:5000
```

**Step 6 – Login**
| Username | Password | Role |
|---|---|---|
| admin | admin123 | Admin (full access) |
| organizer | org123 | Organizer (create/edit) |
| viewer | view123 | Viewer (read only) |

---

### Option B: Docker

```bash
docker-compose up --build
```
Then open `http://localhost:5000`

---

## 🧪 Running Tests

```bash
# Make sure venv is active and you are in the project root
python -m pytest tests/ -v
```

Expected output:
```
tests/test_conflicts.py::test_events_overlap_true               PASSED
tests/test_conflicts.py::test_events_overlap_false_adjacent     PASSED
tests/test_conflicts.py::test_events_overlap_false_gap          PASSED
tests/test_conflicts.py::test_room_conflict_detected            PASSED
tests/test_conflicts.py::test_room_no_conflict_sequential       PASSED
tests/test_conflicts.py::test_equipment_conflict_exceeded_quantity  PASSED
tests/test_conflicts.py::test_equipment_no_conflict_within_quantity PASSED
tests/test_conflicts.py::test_instructor_conflict_double_booked     PASSED
tests/test_conflicts.py::test_room_capacity_exceeded            PASSED
tests/test_conflicts.py::test_room_capacity_ok                  PASSED
tests/test_conflicts.py::test_combined_overlap_and_capacity     PASSED

11 passed in X.XXs
```

---

## 🌐 REST API Endpoints

All endpoints require login session.

| Method | URL | Description |
|---|---|---|
| GET | `/api/events` | List all active events |
| POST | `/api/events` | Create event (organizer+) |
| GET | `/api/allocations` | List all allocations |
| POST | `/api/allocations` | Create allocation (organizer+) |
| GET | `/api/resources` | List all active resources |
| POST | `/api/check-conflict` | Check conflict before allocating |

---

## 🗂 Project Structure

```
app.py              ← entire application (single-file)
requirements.txt    ← pip dependencies
Dockerfile          ← Docker build
docker-compose.yml  ← Docker Compose
README.md           ← this file
tests/
  __init__.py
  test_conflicts.py ← 11 unit tests for conflict engine
```

---

## 🔑 Roles & Permissions

| Action | Admin | Organizer | Viewer |
|---|---|---|---|
| View events / resources | ✅ | ✅ | ✅ |
| Create / edit events | ✅ | ✅ | ❌ |
| Create / edit resources | ✅ | ✅ | ❌ |
| Delete resources | ✅ | ❌ | ❌ |
| Manage users | ✅ | ❌ | ❌ |

---

