"""
Unit tests for the conflict detection engine.
Aerele Technologies – Flask Hiring Test Assignment v2
Tests 8 overlap/conflict scenarios as required.

Run with:
    python -m pytest tests/ -v
"""
import pytest
from datetime import datetime, timedelta

# ── import from the single-file app ──────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import app as flask_app, db, User, Event, Resource, Allocation
from app import events_overlap, check_conflicts, check_capacity


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────
@pytest.fixture(scope='function')
def app():
    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-secret',
    })
    with flask_app.app_context():
        db.create_all()
        admin = User(username='testadmin', email='admin@test.com', role='admin')
        admin.set_password('password')
        db.session.add(admin)
        db.session.commit()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def make_event(title, start_offset_h, duration_h, attendees=10):
    base  = datetime(2024, 6, 1, 8, 0)
    start = base + timedelta(hours=start_offset_h)
    end   = start + timedelta(hours=duration_h)
    admin = User.query.filter_by(username='testadmin').first()
    ev    = Event(title=title, start_time=start, end_time=end,
                  attendees=attendees, created_by=admin.id)
    db.session.add(ev)
    db.session.commit()
    return ev


def make_room(name, capacity=20):
    r = Resource(name=name, type='room', capacity=capacity)
    db.session.add(r); db.session.commit(); return r


def make_equipment(name, quantity=5):
    r = Resource(name=name, type='equipment', quantity=quantity)
    db.session.add(r); db.session.commit(); return r


def make_instructor(name):
    r = Resource(name=name, type='instructor')
    db.session.add(r); db.session.commit(); return r


def allocate(event, resource, qty=1):
    a = Allocation(event_id=event.id, resource_id=resource.id, quantity_used=qty)
    db.session.add(a); db.session.commit(); return a


# ─────────────────────────────────────────────
# TEST 1 – events_overlap pure function
# ─────────────────────────────────────────────
def test_events_overlap_true(ctx):
    """Two events that clearly overlap should return True."""
    t = datetime(2024, 1, 1, 9, 0)
    assert events_overlap(t, t + timedelta(hours=2),
                          t + timedelta(hours=1), t + timedelta(hours=3)) is True


def test_events_overlap_false_adjacent(ctx):
    """Adjacent events (one ends exactly when the next starts) do NOT overlap."""
    t = datetime(2024, 1, 1, 9, 0)
    assert events_overlap(t, t + timedelta(hours=2),
                          t + timedelta(hours=2), t + timedelta(hours=4)) is False


def test_events_overlap_false_gap(ctx):
    """Events with a gap between them do NOT overlap."""
    t = datetime(2024, 1, 1, 9, 0)
    assert events_overlap(t, t + timedelta(hours=1),
                          t + timedelta(hours=2), t + timedelta(hours=3)) is False


# ─────────────────────────────────────────────
# TEST 2 – Room single-occupancy conflict
# ─────────────────────────────────────────────
def test_room_conflict_detected(ctx):
    """Booking the same room for overlapping events should flag a conflict."""
    room = make_room('Hall A')
    ev1  = make_event('Morning Talk', 0, 2)
    allocate(ev1, room)

    ev2      = make_event('Overlap Talk', 1, 2)
    result   = check_conflicts(room.id, ev2.start_time, ev2.end_time)
    assert len(result) > 0
    assert 'Hall A' in result[0]['reason']


def test_room_no_conflict_sequential(ctx):
    """Sequential room bookings (no overlap) should not conflict."""
    room = make_room('Hall B')
    ev1  = make_event('Session 1', 0, 2)
    allocate(ev1, room)

    ev2    = make_event('Session 2', 2, 2)
    result = check_conflicts(room.id, ev2.start_time, ev2.end_time)
    assert result == []


# ─────────────────────────────────────────────
# TEST 3 – Equipment quantity constraint
# ─────────────────────────────────────────────
def test_equipment_conflict_exceeded_quantity(ctx):
    """Requesting more equipment units than available should trigger a conflict."""
    equip = make_equipment('Projector', quantity=2)
    ev1   = make_event('Event A', 0, 3)
    allocate(ev1, equip, qty=2)          # uses all 2 units

    ev2    = make_event('Event B', 1, 2)
    result = check_conflicts(equip.id, ev2.start_time, ev2.end_time, qty_needed=1)
    assert len(result) > 0
    assert 'Projector' in result[0]['reason']


def test_equipment_no_conflict_within_quantity(ctx):
    """Requesting within available quantity should pass."""
    equip = make_equipment('Laptop', quantity=3)
    ev1   = make_event('Workshop A', 0, 2)
    allocate(ev1, equip, qty=2)          # 2 used, 1 free

    ev2    = make_event('Workshop B', 1, 2)
    result = check_conflicts(equip.id, ev2.start_time, ev2.end_time, qty_needed=1)
    assert result == []


# ─────────────────────────────────────────────
# TEST 4 – Instructor single-occupancy
# ─────────────────────────────────────────────
def test_instructor_conflict_double_booked(ctx):
    """Instructor assigned to two overlapping events should be flagged."""
    inst = make_instructor('Dr. Smith')
    ev1  = make_event('Class 1', 0, 2)
    allocate(ev1, inst)

    ev2    = make_event('Class 2', 1, 2)
    result = check_conflicts(inst.id, ev2.start_time, ev2.end_time)
    assert len(result) > 0
    assert 'Dr. Smith' in result[0]['reason']


# ─────────────────────────────────────────────
# TEST 5 – Room capacity vs attendees
# ─────────────────────────────────────────────
def test_room_capacity_exceeded(ctx):
    """Event with more attendees than room capacity should raise a conflict."""
    room   = make_room('Small Room', capacity=10)
    result = check_capacity(room.id, attendees=20)
    assert len(result) > 0
    assert 'Small Room' in result[0]['reason']


def test_room_capacity_ok(ctx):
    """Event with attendees within room capacity should not conflict."""
    room   = make_room('Large Hall', capacity=100)
    result = check_capacity(room.id, attendees=50)
    assert result == []


# ─────────────────────────────────────────────
# TEST 6 – Combined overlap + capacity
# ─────────────────────────────────────────────
def test_combined_overlap_and_capacity(ctx):
    """Both time overlap and capacity violation are caught for the same allocation."""
    room = make_room('Tiny Room', capacity=5)
    ev1  = make_event('Early Event', 0, 3)
    allocate(ev1, room)

    ev2       = make_event('Overlapping Event', 1, 2, attendees=10)
    conflicts = check_conflicts(room.id, ev2.start_time, ev2.end_time)
    capacity  = check_capacity(room.id, ev2.attendees)
    all_c     = conflicts + capacity
    assert len(all_c) >= 1
    reasons = ' '.join(c['reason'] for c in all_c)
    assert 'Tiny Room' in reasons
