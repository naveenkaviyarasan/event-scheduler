"""
Event Scheduling & Resource Allocation System
Aerele Technologies - Flask Hiring Test Assignment v2
"""

import os, csv, io, json
from datetime import datetime, timedelta
from flask import Flask, render_template_string, redirect, flash, request, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'aerele-secret-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///scheduler.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'
login_manager.login_message_category = 'warning'

# ── Models ────────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20), default='viewer')
    # CRITICAL FIX: column named 'active' (not 'is_active')
    # Flask-Login UserMixin.is_active is a property — naming the DB column
    # 'is_active' overwrites it, so login_user() silently fails every time.
    active        = db.Column(db.Boolean, default=True, nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # Flask-Login reads this property to decide if user can log in
    @property
    def is_active(self):
        return bool(self.active)

    def set_password(self, pw):   self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)
    def can_edit(self):           return self.role in ('admin', 'organizer')
    def is_admin(self):           return self.role == 'admin'


class Resource(db.Model):
    __tablename__ = 'resources'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    type        = db.Column(db.String(30),  nullable=False)
    capacity    = db.Column(db.Integer,  nullable=True)
    quantity    = db.Column(db.Integer,  default=1)
    description = db.Column(db.Text,    nullable=True)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    allocations = db.relationship('Allocation', backref='resource', lazy=True)


class Event(db.Model):
    __tablename__ = 'events'
    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    start_time  = db.Column(db.DateTime, nullable=False)
    end_time    = db.Column(db.DateTime, nullable=False)
    timezone    = db.Column(db.String(50), default='Asia/Kolkata')
    attendees   = db.Column(db.Integer, default=1)
    created_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    creator     = db.relationship('User', backref='events')
    allocations = db.relationship('Allocation', backref='event', lazy=True, cascade='all,delete-orphan')

    def duration_hours(self):
        return round((self.end_time - self.start_time).total_seconds() / 3600, 2)


class Allocation(db.Model):
    __tablename__ = 'allocations'
    id            = db.Column(db.Integer, primary_key=True)
    event_id      = db.Column(db.Integer, db.ForeignKey('events.id'),    nullable=False)
    resource_id   = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False)
    quantity_used = db.Column(db.Integer, default=1)
    notes         = db.Column(db.Text, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, nullable=True)
    action    = db.Column(db.String(100), nullable=False)
    details   = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

# ── Conflict Engine ───────────────────────────────────────────────────────────

def events_overlap(s1, e1, s2, e2):
    return s1 < e2 and s2 < e1

def check_conflicts(resource_id, start_time, end_time, exclude_event_id=None, qty_needed=1):
    resource = db.session.get(Resource, resource_id)
    if not resource:
        return [{'reason': 'Resource not found.'}]
    q = (Allocation.query.join(Event)
         .filter(Allocation.resource_id == resource_id,
                 Event.is_active == True,
                 Event.start_time < end_time,
                 Event.end_time > start_time))
    if exclude_event_id:
        q = q.filter(Event.id != exclude_event_id)
    overlaps = q.all()
    if not overlaps:
        return []
    if resource.type in ('room', 'instructor'):
        titles = ', '.join(f'"{a.event.title}"' for a in overlaps)
        return [{'reason': f'"{resource.name}" already booked: {titles}'}]
    if resource.type == 'equipment':
        used  = sum(a.quantity_used or 1 for a in overlaps)
        avail = (resource.quantity or 1) - used
        if qty_needed > avail:
            return [{'reason': f'"{resource.name}": only {avail} unit(s) free, need {qty_needed}'}]
    return []

def check_capacity(resource_id, attendees):
    r = db.session.get(Resource, resource_id)
    if r and r.type == 'room' and r.capacity and attendees and attendees > r.capacity:
        return [{'reason': f'Room "{r.name}" capacity {r.capacity} < {attendees} attendees'}]
    return []

def audit(action, details=''):
    uid = current_user.id if current_user.is_authenticated else None
    db.session.add(AuditLog(user_id=uid, action=action, details=details))
    db.session.commit()

# ── Template helpers ──────────────────────────────────────────────────────────

BASE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{title}} - EventScheduler</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
body{background:#f1f5f9;font-family:"Segoe UI",system-ui,sans-serif}
.sidebar{width:230px;min-height:100vh;background:#1e293b;position:fixed;top:0;left:0;z-index:100}
.sidebar .brand{padding:1.2rem 1rem;font-size:1rem;font-weight:700;color:#fff;border-bottom:1px solid #334155}
.sidebar .nav-link{color:#94a3b8;padding:.55rem 1rem;border-radius:6px;margin:2px 6px;font-size:.875rem}
.sidebar .nav-link:hover,.sidebar .nav-link.active{background:#334155;color:#fff}
.sidebar .nav-link i{margin-right:.4rem}
.sidebar .sec-label{font-size:.65rem;text-transform:uppercase;letter-spacing:.08em;color:#475569;padding:.6rem 1rem .2rem}
.main{margin-left:230px;padding:1.5rem}
.topbar{background:#fff;border-bottom:1px solid #e2e8f0;padding:.65rem 1.5rem;margin-left:230px;
        position:sticky;top:0;z-index:99;display:flex;align-items:center;justify-content:space-between}
.card{border:none;box-shadow:0 1px 3px rgba(0,0,0,.08);border-radius:10px}
.badge-admin{background:#dc2626}.badge-organizer{background:#2563eb}.badge-viewer{background:#64748b}
</style>
%%EXTRA_CSS%%
</head><body>
{% if current_user.is_authenticated %}
<div class="sidebar">
  <div class="brand"><i class="bi bi-calendar-event"></i> EventScheduler</div>
  <nav class="py-2">
    <div class="sec-label">Events</div>
    <a href="/events"      class="nav-link {% if pg=='events'    %}active{% endif %}"><i class="bi bi-calendar3"></i> Events</a>
    <a href="/calendar"    class="nav-link {% if pg=='calendar'  %}active{% endif %}"><i class="bi bi-grid-3x3-gap"></i> Calendar</a>
    <div class="sec-label">Resources</div>
    <a href="/resources"   class="nav-link {% if pg=='resources' %}active{% endif %}"><i class="bi bi-box-seam"></i> Resources</a>
    <a href="/allocations" class="nav-link {% if pg=='alloc'     %}active{% endif %}"><i class="bi bi-diagram-3"></i> Allocations</a>
    <div class="sec-label">Reports</div>
    <a href="/reports"     class="nav-link {% if pg=='reports'   %}active{% endif %}"><i class="bi bi-bar-chart-line"></i> Utilisation</a>
    <a href="/reports/csv" class="nav-link"><i class="bi bi-download"></i> Export CSV</a>
    {% if current_user.is_admin() %}
    <div class="sec-label">Admin</div>
    <a href="/users"       class="nav-link {% if pg=='users'     %}active{% endif %}"><i class="bi bi-people"></i> Users</a>
    {% endif %}
    {% if current_user.is_admin() %}
    <a href="/audit" class="nav-link {% if pg=='audit' %}active{% endif %}"><i class="bi bi-clock-history"></i> Audit Log</a>
    {% endif %}
    <div class="sec-label">API</div>
    <a href="/api/events" class="nav-link" target="_blank"><i class="bi bi-code-slash"></i> REST API</a>
    <div class="mt-3 px-2">
      <a href="/logout" class="nav-link text-danger"><i class="bi bi-box-arrow-left"></i> Logout</a>
    </div>
  </nav>
</div>
<div class="topbar">
  <span class="fw-semibold text-secondary small">%%HEADING%%</span>
  <div class="d-flex align-items-center gap-2">
    <span class="badge badge-{{current_user.role}} text-white">{{current_user.role}}</span>
    <span class="text-muted small">{{current_user.username}}</span>
  </div>
</div>
{% endif %}
<div class="{% if current_user.is_authenticated %}main{% else %}container mt-5{% endif %}">
  {% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat,msg in msgs %}
  <div class="alert alert-{{cat}} alert-dismissible fade show">
    {{msg}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>
  </div>
  {% endfor %}{% endwith %}
  %%CONTENT%%
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
%%EXTRA_JS%%
</body></html>"""


def render(body, **kw):
    html = BASE.replace('%%EXTRA_CSS%%', kw.pop('extra_css', ''))
    html = html.replace('%%EXTRA_JS%%',  kw.pop('extra_js',  ''))
    html = html.replace('%%HEADING%%',   kw.pop('heading', ''))
    html = html.replace('%%CONTENT%%',   body)
    return render_template_string(html, **kw)

# ── Auth ──────────────────────────────────────────────────────────────────────

LOGIN_HTML = """
<div class="row justify-content-center">
 <div class="col-md-4">
  <div class="text-center mb-4">
   <h3 class="fw-bold"><i class="bi bi-calendar-event text-primary"></i> EventScheduler</h3>
   <p class="text-muted small">Aerele Technologies</p>
  </div>
  <div class="card p-4">
   <h5 class="mb-3">Sign In</h5>
   <form method="POST">
    <div class="mb-3">
     <label class="form-label fw-semibold">Username</label>
     <input name="username" class="form-control" autofocus required>
    </div>
    <div class="mb-3">
     <label class="form-label fw-semibold">Password</label>
     <input name="password" type="password" class="form-control" required>
    </div>
    <button type="submit" class="btn btn-primary w-100 py-2">Sign In</button>
   </form>
  </div>
  <p class="text-center mt-3 text-muted small">
   <b>admin</b>/admin123 &nbsp;|&nbsp; <b>organizer</b>/org123 &nbsp;|&nbsp; <b>viewer</b>/view123
  </p>
 </div>
</div>
"""

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if current_user.is_authenticated:
        return redirect('/events')
    if request.method == 'POST':
        uname = request.form.get('username', '').strip()
        pwd   = request.form.get('password', '').strip()
        user  = User.query.filter_by(username=uname).first()
        if user is None:
            flash('User not found.', 'danger')
        elif not user.check_password(pwd):
            flash('Wrong password.', 'danger')
        elif not user.active:
            flash('Account is disabled.', 'danger')
        else:
            login_user(user, remember=True)
            flash(f'Welcome, {user.username}!', 'success')
            return redirect(request.args.get('next') or '/events')
    return render(LOGIN_HTML, title='Login', pg='')


@app.route('/logout')
@login_required
def logout_page():
    logout_user()
    flash('Logged out.', 'info')
    return redirect('/login')


@app.route('/')
def index():
    return redirect('/events' if current_user.is_authenticated else '/login')

# ── Events ────────────────────────────────────────────────────────────────────

EVENTS_LIST = """
<div class="d-flex justify-content-between align-items-center mb-3">
 <form class="d-flex gap-2" method="GET">
  <input name="q" class="form-control form-control-sm" placeholder="Search…" value="{{q}}">
  <button class="btn btn-sm btn-outline-secondary">Search</button>
 </form>
 {% if current_user.can_edit() %}
 <a href="/events/new" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> New Event</a>
 {% endif %}
</div>
<div class="card">
 <div class="table-responsive">
  <table class="table table-hover mb-0">
   <thead class="table-light">
    <tr><th>Title</th><th>Start</th><th>End</th><th>Hrs</th><th>Attendees</th><th>Resources</th><th>Actions</th></tr>
   </thead><tbody>
   {% for e in events %}
   <tr>
    <td><a href="/events/{{e.id}}" class="fw-semibold text-decoration-none">{{e.title}}</a></td>
    <td class="small">{{e.start_time.strftime('%Y-%m-%d %H:%M')}}</td>
    <td class="small">{{e.end_time.strftime('%Y-%m-%d %H:%M')}}</td>
    <td class="small text-muted">{{e.duration_hours()}}h</td>
    <td>{{e.attendees or '-'}}</td>
    <td>{% for a in e.allocations %}<span class="badge bg-light text-dark border me-1">{{a.resource.name}}</span>{% endfor %}</td>
    <td>
     <a href="/events/{{e.id}}" class="btn btn-sm btn-outline-secondary">View</a>
     {% if current_user.can_edit() %}
     <a href="/events/{{e.id}}/edit" class="btn btn-sm btn-outline-primary">Edit</a>
     <form method="POST" action="/events/{{e.id}}/delete" class="d-inline" onsubmit="return confirm('Delete?')">
      <button class="btn btn-sm btn-outline-danger">Del</button>
     </form>{% endif %}
    </td>
   </tr>
   {% else %}
   <tr><td colspan="7" class="text-center text-muted py-4">No events found.</td></tr>
   {% endfor %}
   </tbody>
  </table>
 </div>
</div>
"""

EVENT_FORM = """
<div class="row"><div class="col-md-7"><div class="card p-4">
 <form method="POST">
  <div class="mb-3">
   <label class="form-label fw-semibold">Title *</label>
   <input name="title" class="form-control" value="{{ev.title if ev else ''}}" required>
  </div>
  <div class="row">
   <div class="col-md-6 mb-3">
    <label class="form-label fw-semibold">Start *</label>
    <input name="start_time" type="datetime-local" class="form-control"
           value="{{ev.start_time.strftime('%Y-%m-%dT%H:%M') if ev else ''}}" required>
   </div>
   <div class="col-md-6 mb-3">
    <label class="form-label fw-semibold">End *</label>
    <input name="end_time" type="datetime-local" class="form-control"
           value="{{ev.end_time.strftime('%Y-%m-%dT%H:%M') if ev else ''}}" required>
   </div>
  </div>
  <div class="row">
   <div class="col-md-6 mb-3">
    <label class="form-label fw-semibold">Timezone</label>
    <select name="timezone" class="form-select">
     {% for tz in tzlist %}<option value="{{tz}}" {{'selected' if ev and ev.timezone==tz}}>{{tz}}</option>{% endfor %}
    </select>
   </div>
   <div class="col-md-6 mb-3">
    <label class="form-label fw-semibold">Attendees</label>
    <input name="attendees" type="number" min="1" class="form-control" value="{{ev.attendees if ev else ''}}">
   </div>
  </div>
  <div class="mb-3">
   <label class="form-label fw-semibold">Description</label>
   <textarea name="description" class="form-control" rows="3">{{ev.description if ev else ''}}</textarea>
  </div>
  <div class="d-flex gap-2">
   <button type="submit" class="btn btn-primary">Save</button>
   <a href="/events" class="btn btn-outline-secondary">Cancel</a>
  </div>
 </form>
</div></div></div>
"""

EVENT_DETAIL = """
<div class="row">
 <div class="col-md-8">
  <div class="card p-4 mb-3">
   <div class="d-flex justify-content-between">
    <div><h4 class="fw-bold mb-1">{{ev.title}}</h4><span class="badge bg-primary">{{ev.timezone}}</span></div>
    {% if current_user.can_edit() %}
    <div class="d-flex gap-2">
     <a href="/events/{{ev.id}}/edit" class="btn btn-sm btn-outline-primary"><i class="bi bi-pencil"></i> Edit</a>
     <form method="POST" action="/events/{{ev.id}}/delete" onsubmit="return confirm('Delete?')">
      <button class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i> Delete</button>
     </form>
    </div>{% endif %}
   </div><hr>
   <div class="row g-3">
    <div class="col-md-6"><div class="text-muted small">Start</div><div class="fw-semibold">{{ev.start_time.strftime('%a, %d %b %Y %H:%M')}}</div></div>
    <div class="col-md-6"><div class="text-muted small">End</div><div class="fw-semibold">{{ev.end_time.strftime('%a, %d %b %Y %H:%M')}}</div></div>
    <div class="col-md-6"><div class="text-muted small">Duration</div><div class="fw-semibold">{{ev.duration_hours()}}h</div></div>
    <div class="col-md-6"><div class="text-muted small">Attendees</div><div class="fw-semibold">{{ev.attendees or '-'}}</div></div>
    {% if ev.description %}<div class="col-12"><div class="text-muted small">Description</div><div>{{ev.description}}</div></div>{% endif %}
   </div>
  </div>
  <div class="card p-4">
   <div class="d-flex justify-content-between mb-3">
    <h6 class="mb-0 fw-semibold">Resources</h6>
    {% if current_user.can_edit() %}
    <a href="/allocations/new?event_id={{ev.id}}" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> Add</a>
    {% endif %}
   </div>
   {% if ev.allocations %}
   <table class="table table-sm"><thead class="table-light"><tr><th>Resource</th><th>Type</th><th>Qty</th><th>Notes</th><th></th></tr></thead><tbody>
   {% for a in ev.allocations %}
   <tr>
    <td><b>{{a.resource.name}}</b></td>
    <td><span class="badge bg-secondary">{{a.resource.type}}</span></td>
    <td>{{a.quantity_used or 1}}</td>
    <td class="text-muted small">{{a.notes or '-'}}</td>
    <td>{% if current_user.can_edit() %}
     <form method="POST" action="/allocations/{{a.id}}/delete" onsubmit="return confirm('Remove?')">
      <button class="btn btn-sm btn-outline-danger">Remove</button>
     </form>{% endif %}</td>
   </tr>
   {% endfor %}
   </tbody></table>
   {% else %}<p class="text-muted small">No resources yet.</p>{% endif %}
  </div>
 </div>
 <div class="col-md-4">
  <div class="card p-3">
   <h6 class="fw-semibold mb-2">Meta</h6>
   <div class="text-muted small">Created by</div><div class="mb-2">{{ev.creator.username}}</div>
   <div class="text-muted small">Created at</div><div>{{ev.created_at.strftime('%Y-%m-%d %H:%M')}}</div>
  </div>
 </div>
</div>
"""

TZLIST = ['Asia/Kolkata','UTC','America/New_York','America/Los_Angeles',
          'Europe/London','Europe/Paris','Asia/Tokyo','Australia/Sydney']

@app.route('/events')
@login_required
def events_list():
    q = request.args.get('q','')
    qry = Event.query.filter_by(is_active=True)
    if q: qry = qry.filter(Event.title.ilike(f'%{q}%'))
    return render(EVENTS_LIST, title='Events', pg='events', heading='Events',
                  events=qry.order_by(Event.start_time).all(), q=q)

@app.route('/events/new', methods=['GET','POST'])
@login_required
def event_new():
    if not current_user.can_edit(): flash('Permission denied.','danger'); return redirect('/events')
    if request.method == 'POST':
        f = request.form
        try:
            s, e = datetime.fromisoformat(f['start_time']), datetime.fromisoformat(f['end_time'])
            if e <= s: flash('End must be after start.','danger')
            else:
                ev = Event(title=f['title'], description=f.get('description'),
                           start_time=s, end_time=e, timezone=f.get('timezone','Asia/Kolkata'),
                           attendees=int(f['attendees']) if f.get('attendees') else None,
                           created_by=current_user.id)
                db.session.add(ev); db.session.commit()
                audit('create_event', ev.title)
                flash(f'Event created!','success'); return redirect(f'/events/{ev.id}')
        except Exception as ex: flash(f'Error: {ex}','danger')
    return render(EVENT_FORM, title='New Event', pg='events', heading='New Event', ev=None, tzlist=TZLIST)

@app.route('/events/<int:eid>')
@login_required
def event_detail(eid):
    ev = Event.query.filter_by(id=eid,is_active=True).first_or_404()
    return render(EVENT_DETAIL, title=ev.title, pg='events', heading=ev.title, ev=ev)

@app.route('/events/<int:eid>/edit', methods=['GET','POST'])
@login_required
def event_edit(eid):
    if not current_user.can_edit(): flash('Permission denied.','danger'); return redirect('/events')
    ev = Event.query.filter_by(id=eid,is_active=True).first_or_404()
    if request.method == 'POST':
        f = request.form
        try:
            s, e = datetime.fromisoformat(f['start_time']), datetime.fromisoformat(f['end_time'])
            if e <= s: flash('End must be after start.','danger')
            else:
                ev.title=f['title']; ev.description=f.get('description')
                ev.start_time=s; ev.end_time=e; ev.timezone=f.get('timezone','Asia/Kolkata')
                ev.attendees=int(f['attendees']) if f.get('attendees') else None
                db.session.commit(); audit('update_event', ev.title)
                flash('Event updated!','success'); return redirect(f'/events/{ev.id}')
        except Exception as ex: flash(f'Error: {ex}','danger')
    return render(EVENT_FORM, title='Edit Event', pg='events', heading='Edit Event', ev=ev, tzlist=TZLIST)

@app.route('/events/<int:eid>/delete', methods=['POST'])
@login_required
def event_delete(eid):
    if not current_user.can_edit(): flash('Permission denied.','danger'); return redirect('/events')
    ev = Event.query.filter_by(id=eid,is_active=True).first_or_404()
    ev.is_active=False; db.session.commit()
    audit('delete_event', ev.title); flash(f'Deleted "{ev.title}".','success')
    return redirect('/events')

# ── Calendar ──────────────────────────────────────────────────────────────────

CAL_HTML = """
<div class="d-flex justify-content-between align-items-center mb-3">
 <div class="d-flex gap-2 align-items-center">
  <button class="btn btn-sm btn-outline-secondary" id="prev">&#8592;</button>
  <span class="fw-semibold" id="lbl"></span>
  <button class="btn btn-sm btn-outline-secondary" id="next">&#8594;</button>
  <button class="btn btn-sm btn-outline-primary" id="tod">Today</button>
 </div>
 {% if current_user.can_edit() %}
 <a href="/events/new" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> New Event</a>
 {% endif %}
</div>
<div id="cal" class="card p-0 overflow-hidden"></div>
<style>
.cg{display:grid;grid-template-columns:55px repeat(7,1fr)}
.ch{background:#f8fafc;padding:.5rem .3rem;text-align:center;font-size:.75rem;font-weight:600;border-bottom:2px solid #e2e8f0;border-right:1px solid #e2e8f0}
.ct{font-size:.65rem;color:#94a3b8;text-align:right;padding:0 6px;border-right:1px solid #e2e8f0;height:48px;display:flex;align-items:flex-start;padding-top:3px}
.cc{border-right:1px solid #f1f5f9;border-bottom:1px solid #f1f5f9;height:48px;position:relative}
.cc.tc{background:#eff6ff}
.ce{position:absolute;left:2px;right:2px;border-radius:4px;padding:2px 4px;font-size:.68rem;font-weight:600;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;background:#2563eb;color:#fff;text-decoration:none;z-index:2}
.ce:hover{opacity:.8;color:#fff}
</style>
<script>
const EVS={{evs|safe}};
const DN=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
let ws=wkStart(new Date());
function wkStart(d){const x=new Date(d);x.setHours(0,0,0,0);x.setDate(x.getDate()-x.getDay());return x;}
function fmt(d){return d.toLocaleDateString('en-GB',{day:'numeric',month:'short'});}
function build(){
 const wd=Array.from({length:7},(_,i)=>{const d=new Date(ws);d.setDate(d.getDate()+i);return d;});
 const td=new Date();td.setHours(0,0,0,0);
 document.getElementById('lbl').textContent=fmt(wd[0])+' - '+fmt(wd[6]);
 let h='<div class="cg"><div class="ch" style="background:#fff"></div>';
 wd.forEach((d,i)=>{const t=d.getTime()===td.getTime();h+=`<div class="ch ${t?'text-primary':''}">${DN[i]}<br>${d.getDate()}</div>`;});
 for(let hr=0;hr<24;hr++){
  h+=`<div class="ct">${String(hr).padStart(2,'0')}:00</div>`;
  wd.forEach((d,di)=>{h+=`<div class="cc${d.getTime()===td.getTime()?' tc':''}" id="c${di}-${hr}"></div>`;});
 }
 h+='</div>';
 document.getElementById('cal').innerHTML=h;
 EVS.forEach(ev=>{
  const s=new Date(ev.start),e=new Date(ev.end);
  wd.forEach((d,di)=>{
   const ds=new Date(d);ds.setHours(0,0,0,0);const de=new Date(d);de.setHours(23,59,59,999);
   if(s>de||e<ds)return;
   const es=s>ds?s:ds,ee=e<de?e:de;
   const sh=es.getHours()+es.getMinutes()/60,eh=ee.getHours()+ee.getMinutes()/60;
   const c=document.getElementById(`c${di}-${Math.floor(sh)}`);if(!c)return;
   const el=document.createElement('a');
   el.className='ce';el.href=`/events/${ev.id}`;el.title=ev.title;el.textContent=ev.title;
   el.style.top=(sh%1*100)+'%';el.style.height=Math.max((eh-sh)*48,16)+'px';
   c.appendChild(el);
  });
 });
}
document.getElementById('prev').onclick=()=>{ws.setDate(ws.getDate()-7);build();};
document.getElementById('next').onclick=()=>{ws.setDate(ws.getDate()+7);build();};
document.getElementById('tod').onclick=()=>{ws=wkStart(new Date());build();};
build();
</script>
"""

@app.route('/calendar')
@login_required
def calendar_view():
    evs = Event.query.filter_by(is_active=True).all()
    evs_json = json.dumps([{'id':e.id,'title':e.title,'start':e.start_time.isoformat(),'end':e.end_time.isoformat()} for e in evs])
    return render(CAL_HTML, title='Calendar', pg='calendar', heading='Weekly Calendar', evs=evs_json)

# ── Resources ─────────────────────────────────────────────────────────────────

RES_LIST = """
<div class="d-flex justify-content-between align-items-center mb-3">
 <div class="btn-group btn-group-sm">
  <a href="/resources"              class="btn {% if not rtype %}btn-primary{% else %}btn-outline-secondary{% endif %}">All</a>
  <a href="/resources?type=room"       class="btn {% if rtype=='room' %}btn-primary{% else %}btn-outline-secondary{% endif %}">Rooms</a>
  <a href="/resources?type=instructor" class="btn {% if rtype=='instructor' %}btn-primary{% else %}btn-outline-secondary{% endif %}">Instructors</a>
  <a href="/resources?type=equipment"  class="btn {% if rtype=='equipment' %}btn-primary{% else %}btn-outline-secondary{% endif %}">Equipment</a>
 </div>
 {% if current_user.can_edit() %}
 <a href="/resources/new" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> New</a>
 {% endif %}
</div>
<div class="row g-3">
{% for r in resources %}
<div class="col-md-4"><div class="card p-3 h-100">
 <div class="d-flex justify-content-between">
  <div>
   <span class="badge mb-1 {% if r.type=='room' %}bg-info text-dark{% elif r.type=='instructor' %}bg-success{% else %}bg-warning text-dark{% endif %}">{{r.type}}</span>
   <a href="/resources/{{r.id}}" class="text-decoration-none text-dark"><h6 class="mb-0 fw-bold">{{r.name}}</h6></a>
  </div>
  {% if current_user.can_edit() %}
  <div class="dropdown">
   <button class="btn btn-sm btn-light" data-bs-toggle="dropdown">&#8942;</button>
   <ul class="dropdown-menu dropdown-menu-end">
    <li><a class="dropdown-item" href="/resources/{{r.id}}/edit">Edit</a></li>
    {% if current_user.is_admin() %}
    <li><form method="POST" action="/resources/{{r.id}}/delete" onsubmit="return confirm('Delete?')">
     <button class="dropdown-item text-danger">Delete</button></form></li>
    {% endif %}
   </ul>
  </div>{% endif %}
 </div>
 <div class="mt-2 text-muted small">
  {% if r.type=='room'      and r.capacity %}<i class="bi bi-people"></i> Cap: {{r.capacity}}{% endif %}
  {% if r.type=='equipment' and r.quantity  %}<i class="bi bi-stack"></i> Qty: {{r.quantity}}{% endif %}
 </div>
 {% if r.description %}<div class="mt-1 text-muted small">{{r.description[:80]}}</div>{% endif %}
</div></div>
{% else %}
<div class="col-12"><div class="card p-5 text-center text-muted">No resources. <a href="/resources/new">Add one</a>.</div></div>
{% endfor %}
</div>
"""

RES_FORM = """
<div class="row"><div class="col-md-6"><div class="card p-4">
 <form method="POST">
  <div class="mb-3"><label class="form-label fw-semibold">Name *</label>
   <input name="name" class="form-control" value="{{r.name if r else ''}}" required></div>
  <div class="mb-3"><label class="form-label fw-semibold">Type *</label>
   <select name="type" class="form-select" id="rtype">
    <option value="room"       {{'selected' if r and r.type=='room'}}>Room</option>
    <option value="instructor" {{'selected' if r and r.type=='instructor'}}>Instructor</option>
    <option value="equipment"  {{'selected' if r and r.type=='equipment'}}>Equipment</option>
   </select></div>
  <div id="cg" class="mb-3"><label class="form-label fw-semibold">Capacity</label>
   <input name="capacity" type="number" min="1" class="form-control" value="{{r.capacity if r else ''}}"></div>
  <div id="qg" class="mb-3" style="display:none"><label class="form-label fw-semibold">Quantity</label>
   <input name="quantity" type="number" min="1" class="form-control" value="{{r.quantity if r else 1}}"></div>
  <div class="mb-3"><label class="form-label fw-semibold">Description</label>
   <textarea name="description" class="form-control" rows="3">{{r.description if r else ''}}</textarea></div>
  <div class="d-flex gap-2">
   <button type="submit" class="btn btn-primary">Save</button>
   <a href="/resources" class="btn btn-outline-secondary">Cancel</a>
  </div>
 </form>
</div></div></div>
<script>
function upd(){const t=document.getElementById('rtype').value;
 document.getElementById('cg').style.display=t==='room'?'':'none';
 document.getElementById('qg').style.display=t==='equipment'?'':'none';}
document.getElementById('rtype').addEventListener('change',upd);upd();
</script>
"""

@app.route('/resources')
@login_required
def resources_list():
    rtype = request.args.get('type','')
    q = Resource.query.filter_by(is_active=True)
    if rtype: q = q.filter_by(type=rtype)
    return render(RES_LIST, title='Resources', pg='resources', heading='Resources',
                  resources=q.order_by(Resource.name).all(), rtype=rtype)

@app.route('/resources/new', methods=['GET','POST'])
@login_required
def resource_new():
    if not current_user.can_edit(): flash('Permission denied.','danger'); return redirect('/resources')
    if request.method == 'POST':
        f = request.form
        res = Resource(name=f['name'], type=f['type'],
                       capacity=int(f['capacity']) if f.get('capacity') else None,
                       quantity=int(f['quantity']) if f.get('quantity') else 1,
                       description=f.get('description'))
        db.session.add(res); db.session.commit()
        audit('create_resource', res.name); flash(f'Resource created!','success')
        return redirect('/resources')
    return render(RES_FORM, title='New Resource', pg='resources', heading='New Resource', r=None)

@app.route('/resources/<int:rid>/edit', methods=['GET','POST'])
@login_required
def resource_edit(rid):
    if not current_user.can_edit(): flash('Permission denied.','danger'); return redirect('/resources')
    res = Resource.query.filter_by(id=rid,is_active=True).first_or_404()
    if request.method == 'POST':
        f = request.form
        res.name=f['name']; res.type=f['type']
        res.capacity=int(f['capacity']) if f.get('capacity') else None
        res.quantity=int(f['quantity']) if f.get('quantity') else 1
        res.description=f.get('description')
        db.session.commit(); audit('update_resource', res.name)
        flash('Resource updated!','success'); return redirect('/resources')
    return render(RES_FORM, title='Edit Resource', pg='resources', heading='Edit Resource', r=res)

@app.route('/resources/<int:rid>/delete', methods=['POST'])
@login_required
def resource_delete(rid):
    if not current_user.is_admin(): flash('Admin only.','danger'); return redirect('/resources')
    res = Resource.query.filter_by(id=rid,is_active=True).first_or_404()
    res.is_active=False; db.session.commit()
    flash(f'Deleted "{res.name}".','success'); return redirect('/resources')

# ── Allocations ───────────────────────────────────────────────────────────────

ALLOC_LIST = """
<div class="d-flex justify-content-between mb-3">
 <span class="text-muted small">{{allocs|length}} allocation(s)</span>
 {% if current_user.can_edit() %}
 <a href="/allocations/new" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> New</a>
 {% endif %}
</div>
<div class="card"><div class="table-responsive">
 <table class="table table-hover mb-0">
  <thead class="table-light">
   <tr><th>Event</th><th>Time</th><th>Resource</th><th>Type</th><th>Qty</th><th>Notes</th>
   {% if current_user.can_edit() %}<th></th>{% endif %}</tr>
  </thead><tbody>
  {% for a in allocs %}
  <tr>
   <td><a href="/events/{{a.event.id}}" class="fw-semibold text-decoration-none">{{a.event.title}}</a></td>
   <td class="small text-muted">{{a.event.start_time.strftime('%Y-%m-%d %H:%M')}} → {{a.event.end_time.strftime('%H:%M')}}</td>
   <td>{{a.resource.name}}</td>
   <td><span class="badge {% if a.resource.type=='room' %}bg-info text-dark{% elif a.resource.type=='instructor' %}bg-success{% else %}bg-warning text-dark{% endif %}">{{a.resource.type}}</span></td>
   <td>{{a.quantity_used or 1}}</td>
   <td class="text-muted small">{{a.notes or '-'}}</td>
   {% if current_user.can_edit() %}
   <td><form method="POST" action="/allocations/{{a.id}}/delete" onsubmit="return confirm('Remove?')">
    <button class="btn btn-sm btn-outline-danger">Remove</button></form></td>
   {% endif %}
  </tr>
  {% else %}
  <tr><td colspan="7" class="text-center text-muted py-4">No allocations.</td></tr>
  {% endfor %}
  </tbody>
 </table>
</div></div>
"""

ALLOC_FORM = """
<div class="row">
 <div class="col-md-7"><div class="card p-4">
  <div id="cbox" style="display:none" class="mb-3"></div>
  <form method="POST">
   <div class="mb-3"><label class="form-label fw-semibold">Event *</label>
    <select name="event_id" class="form-select" id="evs" required>
     <option value="">— select event —</option>
     {% for e in events %}
     <option value="{{e.id}}" {{'selected' if sel==e.id}}>{{e.title}} ({{e.start_time.strftime('%Y-%m-%d %H:%M')}})</option>
     {% endfor %}
    </select></div>
   <div class="mb-3"><label class="form-label fw-semibold">Resource *</label>
    <select name="resource_id" class="form-select" id="res" required>
     <option value="">— select resource —</option>
     {% for r in resources %}<option value="{{r.id}}">{{r.name}} [{{r.type}}]</option>{% endfor %}
    </select></div>
   <div class="mb-3"><label class="form-label fw-semibold">Quantity</label>
    <input name="quantity_used" type="number" min="1" value="1" class="form-control" id="qty">
    <div class="form-text">For equipment only. Use 1 for rooms/instructors.</div></div>
   <div class="mb-3"><label class="form-label fw-semibold">Notes</label>
    <textarea name="notes" class="form-control" rows="2"></textarea></div>
   <div class="d-flex gap-2">
    <button type="button" class="btn btn-outline-secondary" id="chk">Check Conflicts</button>
    <button type="submit" class="btn btn-primary">Allocate</button>
    <a href="/allocations" class="btn btn-outline-secondary">Cancel</a>
   </div>
  </form>
 </div></div>
 <div class="col-md-5"><div class="card p-3">
  <h6 class="fw-semibold mb-2">Rules</h6>
  <ul class="list-unstyled small text-muted mb-0">
   <li class="mb-2"><span class="badge bg-info text-dark me-1">Room</span> One event at a time.</li>
   <li class="mb-2"><span class="badge bg-success me-1">Instructor</span> One event at a time.</li>
   <li class="mb-2"><span class="badge bg-warning text-dark me-1">Equipment</span> Shared up to quantity.</li>
   <li class="mb-2"><span class="badge bg-secondary me-1">Capacity</span> Room cap ≥ attendees.</li>
  </ul>
 </div></div>
</div>
<script>
function doChk(){
 const eid=document.getElementById('evs').value;
 const rid=document.getElementById('res').value;
 const qty=parseInt(document.getElementById('qty').value)||1;
 const box=document.getElementById('cbox');
 if(!eid||!rid){box.style.display='none';return;}
 fetch('/api/check-conflict',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({event_id:parseInt(eid),resource_id:parseInt(rid),quantity:qty})
 }).then(r=>r.json()).then(d=>{
  box.style.display='';
  box.innerHTML=d.has_conflict
   ? d.conflicts.map(c=>`<div class="alert alert-danger py-2 mb-1"><i class="bi bi-exclamation-triangle-fill me-1"></i>${c}</div>`).join('')
   : '<div class="alert alert-success py-2"><i class="bi bi-check-circle-fill me-1"></i> No conflicts!</div>';
 });
}
document.getElementById('chk').onclick=doChk;
document.getElementById('evs').onchange=doChk;
document.getElementById('res').onchange=doChk;
</script>
"""

@app.route('/allocations')
@login_required
def alloc_list():
    allocs = Allocation.query.join(Event).filter(Event.is_active==True).order_by(Event.start_time).all()
    return render(ALLOC_LIST, title='Allocations', pg='alloc', heading='Allocations', allocs=allocs)

@app.route('/allocations/new', methods=['GET','POST'])
@login_required
def alloc_new():
    if not current_user.can_edit(): flash('Permission denied.','danger'); return redirect('/allocations')
    if request.method == 'POST':
        f = request.form; eid=int(f['event_id']); rid=int(f['resource_id']); qty=int(f.get('quantity_used') or 1)
        ev = db.session.get(Event, eid)
        if Allocation.query.filter_by(event_id=eid,resource_id=rid).first():
            flash('Already allocated.','warning')
        else:
            c = check_conflicts(rid,ev.start_time,ev.end_time,exclude_event_id=eid,qty_needed=qty) + check_capacity(rid,ev.attendees)
            if c:
                for x in c: flash(f'⚠️ {x["reason"]}','danger')
            else:
                a=Allocation(event_id=eid,resource_id=rid,quantity_used=qty,notes=f.get('notes'))
                db.session.add(a); db.session.commit()
                audit('alloc',f'{eid}<-{rid}'); flash('Allocated!','success'); return redirect('/allocations')
    events    = Event.query.filter_by(is_active=True).order_by(Event.start_time).all()
    resources = Resource.query.filter_by(is_active=True).order_by(Resource.name).all()
    sel = request.args.get('event_id',type=int)
    return render(ALLOC_FORM, title='New Allocation', pg='alloc', heading='New Allocation',
                  events=events, resources=resources, sel=sel)

@app.route('/allocations/<int:aid>/delete', methods=['POST'])
@login_required
def alloc_delete(aid):
    if not current_user.can_edit(): flash('Permission denied.','danger'); return redirect('/allocations')
    a = db.session.get(Allocation, aid)
    if a: db.session.delete(a); db.session.commit(); flash('Removed.','success')
    return redirect(request.referrer or '/allocations')

# ── Reports ───────────────────────────────────────────────────────────────────

RPT_HTML = """
<div class="card p-3 mb-4">
 <form method="GET" class="row g-2 align-items-end">
  <div class="col-md-3"><label class="form-label small fw-semibold">From</label>
   <input name="start" type="datetime-local" class="form-control form-control-sm" value="{{sv}}"></div>
  <div class="col-md-3"><label class="form-label small fw-semibold">To</label>
   <input name="end"   type="datetime-local" class="form-control form-control-sm" value="{{ev}}"></div>
  <div class="col-md-3"><label class="form-label small fw-semibold">Type</label>
   <select name="rtype" class="form-select form-select-sm">
    <option value="">All</option>
    <option value="room"       {{'selected' if rtype=='room'}}>Rooms</option>
    <option value="instructor" {{'selected' if rtype=='instructor'}}>Instructors</option>
    <option value="equipment"  {{'selected' if rtype=='equipment'}}>Equipment</option>
   </select></div>
  <div class="col-md-3 d-flex gap-2">
   <button type="submit" class="btn btn-primary btn-sm">Generate</button>
   <a href="/reports/csv?start={{sv}}&end={{ev}}" class="btn btn-outline-success btn-sm"><i class="bi bi-download"></i> CSV</a>
  </div>
 </form>
</div>
<div class="row g-3 mb-4">
 <div class="col-md-3"><div class="card p-3 text-center"><div class="text-muted small">Period</div><div class="fw-semibold small">{{sd.strftime('%d %b')}} – {{ed.strftime('%d %b %Y')}}</div></div></div>
 <div class="col-md-3"><div class="card p-3 text-center"><div class="text-muted small">Events</div><div class="fw-bold fs-4">{{total}}</div></div></div>
 <div class="col-md-3"><div class="card p-3 text-center"><div class="text-muted small">Resources</div><div class="fw-bold fs-4">{{rows|length}}</div></div></div>
 <div class="col-md-3"><div class="card p-3 text-center"><div class="text-muted small">Avg Util</div><div class="fw-bold fs-4">{{avg}}%</div></div></div>
</div>
<div class="card"><div class="table-responsive">
 <table class="table table-hover mb-0">
  <thead class="table-light">
   <tr><th>Resource</th><th>Type</th><th>Cap/Qty</th><th>Events</th><th>Hours</th><th>Available</th><th>Utilisation</th></tr>
  </thead><tbody>
  {% for r in rows %}
  <tr>
   <td><b>{{r.name}}</b></td>
   <td><span class="badge {% if r.type=='room' %}bg-info text-dark{% elif r.type=='instructor' %}bg-success{% else %}bg-warning text-dark{% endif %}">{{r.type}}</span></td>
   <td class="text-muted small">{{r.cap}}</td>
   <td>{{r.ec}}</td><td>{{r.hu}}h</td><td>{{r.th}}h</td>
   <td style="min-width:150px">
    <div class="d-flex align-items-center gap-2">
     <div class="flex-grow-1"><div class="progress" style="height:8px">
      <div class="progress-bar {% if r.pct>=75 %}bg-danger{% elif r.pct>=40 %}bg-warning{% else %}bg-success{% endif %}"
           style="width:{{[r.pct,100]|min}}%"></div>
     </div></div>
     <span class="small fw-semibold">{{r.pct}}%</span>
    </div>
   </td>
  </tr>
  {% else %}<tr><td colspan="7" class="text-center text-muted py-4">No data.</td></tr>
  {% endfor %}
  </tbody>
 </table>
</div></div>
"""

def report_data(start_date, end_date, rtype=''):
    q = Resource.query.filter_by(is_active=True)
    if rtype: q = q.filter_by(type=rtype)
    th = (end_date-start_date).total_seconds()/3600
    rows = []
    for r in q.order_by(Resource.name).all():
        als = (Allocation.query.join(Event)
               .filter(Allocation.resource_id==r.id, Event.is_active==True,
                       Event.start_time>=start_date, Event.end_time<=end_date).all())
        hu  = round(sum(a.event.duration_hours() for a in als), 2)
        pct = round(hu/th*100, 2) if th>0 else 0
        cap = (f'{r.capacity} ppl' if r.type=='room' and r.capacity
               else f'{r.quantity} units' if r.type=='equipment' else '—')
        class R: pass
        row=R(); row.name=r.name; row.type=r.type; row.cap=cap
        row.ec=len(als); row.hu=hu; row.th=round(th,2); row.pct=pct
        rows.append(row)
    rows.sort(key=lambda x:x.pct, reverse=True)
    return rows

@app.route('/reports')
@login_required
def reports_view():
    ed = datetime.utcnow(); sd = ed - timedelta(days=30)
    try:
        if request.args.get('start'): sd = datetime.fromisoformat(request.args['start'])
        if request.args.get('end'):   ed = datetime.fromisoformat(request.args['end'])
    except: pass
    rtype = request.args.get('rtype','')
    rows  = report_data(sd, ed, rtype)
    total = Event.query.filter(Event.is_active==True, Event.start_time>=sd, Event.end_time<=ed).count()
    avg   = round(sum(r.pct for r in rows)/len(rows),1) if rows else 0
    return render(RPT_HTML, title='Reports', pg='reports', heading='Utilisation Report',
                  rows=rows, sd=sd, ed=ed, sv=sd.strftime('%Y-%m-%dT%H:%M'),
                  ev=ed.strftime('%Y-%m-%dT%H:%M'), rtype=rtype, total=total, avg=avg)

@app.route('/reports/csv')
@login_required
def reports_csv():
    ed = datetime.utcnow(); sd = ed - timedelta(days=30)
    try:
        if request.args.get('start'): sd = datetime.fromisoformat(request.args['start'])
        if request.args.get('end'):   ed = datetime.fromisoformat(request.args['end'])
    except: pass
    rows = report_data(sd, ed)
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(['Resource','Type','Cap/Qty','Events','Hours','Available','Utilisation%'])
    for r in rows: w.writerow([r.name,r.type,r.cap,r.ec,r.hu,r.th,f'{r.pct}%'])
    out.seek(0)
    fname = f"util_{sd.strftime('%Y%m%d')}_{ed.strftime('%Y%m%d')}.csv"
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':f'attachment;filename={fname}'})

# ── Users ─────────────────────────────────────────────────────────────────────

USERS_HTML = """
<div class="d-flex justify-content-between mb-3">
 <h5 class="mb-0">User Management</h5>
 <a href="/users/new" class="btn btn-sm btn-primary"><i class="bi bi-person-plus"></i> New User</a>
</div>
<div class="card"><div class="table-responsive">
 <table class="table table-hover mb-0">
  <thead class="table-light"><tr><th>#</th><th>Username</th><th>Email</th><th>Role</th><th>Status</th><th>Created</th><th></th></tr></thead>
  <tbody>
  {% for u in users %}
  <tr>
   <td>{{u.id}}</td><td><b>{{u.username}}</b></td><td>{{u.email}}</td>
   <td><span class="badge badge-{{u.role}} text-white">{{u.role}}</span></td>
   <td><span class="badge {{'bg-success' if u.is_active else 'bg-secondary'}}">{{'Active' if u.is_active else 'Inactive'}}</span></td>
   <td class="text-muted small">{{u.created_at.strftime('%Y-%m-%d')}}</td>
   <td>{% if u.id != current_user.id %}
    <form method="POST" action="/users/{{u.id}}/toggle" class="d-inline">
     <button class="btn btn-sm {{'btn-outline-warning' if u.is_active else 'btn-outline-success'}}">
      {{'Deactivate' if u.is_active else 'Activate'}}</button>
    </form>{% else %}<span class="text-muted small">You</span>{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
 </table>
</div></div>
"""

USER_FORM_HTML = """
<div class="row"><div class="col-md-5"><div class="card p-4">
 <form method="POST">
  <div class="mb-3"><label class="form-label fw-semibold">Username *</label>
   <input name="username" class="form-control" required></div>
  <div class="mb-3"><label class="form-label fw-semibold">Email *</label>
   <input name="email" type="email" class="form-control" required></div>
  <div class="mb-3"><label class="form-label fw-semibold">Role</label>
   <select name="role" class="form-select">
    <option value="viewer">Viewer</option>
    <option value="organizer">Organizer</option>
    <option value="admin">Admin</option>
   </select></div>
  <div class="mb-3"><label class="form-label fw-semibold">Password *</label>
   <input name="password" type="password" class="form-control" required></div>
  <div class="d-flex gap-2">
   <button type="submit" class="btn btn-primary">Create</button>
   <a href="/users" class="btn btn-outline-secondary">Cancel</a>
  </div>
 </form>
</div></div></div>
"""

@app.route('/users')
@login_required
def users_list():
    if not current_user.is_admin(): flash('Admin only.','danger'); return redirect('/events')
    return render(USERS_HTML, title='Users', pg='users', heading='Users',
                  users=User.query.order_by(User.created_at.desc()).all())

@app.route('/users/new', methods=['GET','POST'])
@login_required
def user_new():
    if not current_user.is_admin(): flash('Admin only.','danger'); return redirect('/events')
    if request.method == 'POST':
        f = request.form
        if User.query.filter_by(username=f['username']).first():
            flash('Username taken.','danger')
        else:
            u = User(username=f['username'], email=f['email'], role=f.get('role','viewer'))
            u.set_password(f['password'])
            db.session.add(u); db.session.commit()
            flash(f'User created!','success'); return redirect('/users')
    return render(USER_FORM_HTML, title='New User', pg='users', heading='New User')

@app.route('/users/<int:uid>/toggle', methods=['POST'])
@login_required
def user_toggle(uid):
    if not current_user.is_admin(): flash('Admin only.','danger'); return redirect('/events')
    u = db.session.get(User, uid)
    if u and u.id != current_user.id:
        u.active = not u.active
        db.session.commit()
        flash(f'{u.username} {"activated" if u.active else "deactivated"}.','success')
    return redirect('/users')


# ── Audit Log (Admin) ─────────────────────────────────────────────────────────

AUDIT_HTML = """
<div class="d-flex justify-content-between mb-3">
 <h5 class="mb-0">Audit Log</h5>
 <span class="text-muted small">Last 200 actions</span>
</div>
<div class="card"><div class="table-responsive">
 <table class="table table-hover table-sm mb-0">
  <thead class="table-light"><tr><th>Time</th><th>User</th><th>Action</th><th>Details</th></tr></thead>
  <tbody>
  {% for log in logs %}
  <tr>
   <td class="text-muted small text-nowrap">{{log.timestamp.strftime('%Y-%m-%d %H:%M:%S')}}</td>
   <td class="small">{{log.username or 'system'}}</td>
   <td><span class="badge bg-secondary">{{log.action}}</span></td>
   <td class="small text-muted">{{log.details or '-'}}</td>
  </tr>
  {% else %}
  <tr><td colspan="4" class="text-center text-muted py-4">No audit logs yet.</td></tr>
  {% endfor %}
  </tbody>
 </table>
</div></div>
"""

@app.route('/audit')
@login_required
def audit_log():
    if not current_user.is_admin():
        flash('Admin only.', 'danger'); return redirect('/events')
    from sqlalchemy import outerjoin
    logs = (db.session.query(AuditLog, User.username)
            .outerjoin(User, AuditLog.user_id == User.id)
            .order_by(AuditLog.timestamp.desc()).limit(200).all())
    log_list = []
    for entry, uname in logs:
        entry.username = uname
        log_list.append(entry)
    return render(AUDIT_HTML, title='Audit Log', pg='audit', heading='Audit Log', logs=log_list)


# ── Resource Detail ───────────────────────────────────────────────────────────

RES_DETAIL_HTML = """
<div class="row">
 <div class="col-md-7">
  <div class="card p-4 mb-3">
   <div class="d-flex justify-content-between align-items-start">
    <div>
     <span class="badge mb-2 {% if r.type=='room' %}bg-info text-dark{% elif r.type=='instructor' %}bg-success{% else %}bg-warning text-dark{% endif %}">{{r.type}}</span>
     <h4 class="fw-bold mb-1">{{r.name}}</h4>
    </div>
    {% if current_user.can_edit() %}
    <div class="d-flex gap-2">
     <a href="/resources/{{r.id}}/edit" class="btn btn-sm btn-outline-primary"><i class="bi bi-pencil"></i> Edit</a>
     {% if current_user.is_admin() %}
     <form method="POST" action="/resources/{{r.id}}/delete" onsubmit="return confirm('Delete?')">
      <button class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i> Delete</button>
     </form>{% endif %}
    </div>{% endif %}
   </div>
   <hr>
   <div class="row g-3">
    {% if r.type=='room' and r.capacity %}
    <div class="col-md-6"><div class="text-muted small">Capacity</div><div class="fw-semibold">{{r.capacity}} people</div></div>
    {% endif %}
    {% if r.type=='equipment' %}
    <div class="col-md-6"><div class="text-muted small">Total Quantity</div><div class="fw-semibold">{{r.quantity}} units</div></div>
    {% endif %}
    {% if r.description %}
    <div class="col-12"><div class="text-muted small">Description</div><div>{{r.description}}</div></div>
    {% endif %}
   </div>
  </div>
  <div class="card p-4">
   <h6 class="fw-semibold mb-3">Upcoming Allocations</h6>
   {% if allocations %}
   <table class="table table-sm"><thead class="table-light">
    <tr><th>Event</th><th>Start</th><th>End</th><th>Qty</th></tr>
   </thead><tbody>
   {% for a in allocations %}
   <tr>
    <td><a href="/events/{{a.event.id}}" class="text-decoration-none">{{a.event.title}}</a></td>
    <td class="small">{{a.event.start_time.strftime('%Y-%m-%d %H:%M')}}</td>
    <td class="small">{{a.event.end_time.strftime('%H:%M')}}</td>
    <td>{{a.quantity_used or 1}}</td>
   </tr>
   {% endfor %}
   </tbody></table>
   {% else %}<p class="text-muted small">No allocations for this resource.</p>{% endif %}
  </div>
 </div>
 <div class="col-md-5">
  <div class="card p-3">
   <h6 class="fw-semibold mb-2">Stats</h6>
   <div class="text-muted small">Total Bookings</div>
   <div class="fw-bold fs-4 mb-2">{{total_bookings}}</div>
   <div class="text-muted small">Total Hours Used</div>
   <div class="fw-bold fs-4">{{total_hours}}h</div>
  </div>
 </div>
</div>
"""

@app.route('/resources/<int:rid>')
@login_required
def resource_detail(rid):
    r = Resource.query.filter_by(id=rid, is_active=True).first_or_404()
    allocations = (Allocation.query.join(Event)
                   .filter(Allocation.resource_id == rid, Event.is_active == True)
                   .order_by(Event.start_time).all())
    total_bookings = len(allocations)
    total_hours    = round(sum(a.event.duration_hours() for a in allocations), 2)
    return render(RES_DETAIL_HTML, title=r.name, pg='resources', heading=r.name,
                  r=r, allocations=allocations,
                  total_bookings=total_bookings, total_hours=total_hours)

# ── REST API ──────────────────────────────────────────────────────────────────

@app.route('/api/events', methods=['GET'])
@login_required
def api_events():
    return jsonify([{'id':e.id,'title':e.title,'start_time':e.start_time.isoformat(),
                     'end_time':e.end_time.isoformat(),'timezone':e.timezone,'attendees':e.attendees,
                     'resources':[{'id':a.resource_id,'name':a.resource.name} for a in e.allocations]}
                    for e in Event.query.filter_by(is_active=True).order_by(Event.start_time).all()])

@app.route('/api/events', methods=['POST'])
@login_required
def api_create_event():
    if not current_user.can_edit(): return jsonify({'error':'Forbidden'}),403
    d = request.get_json()
    try:
        ev=Event(title=d['title'],start_time=datetime.fromisoformat(d['start_time']),
                 end_time=datetime.fromisoformat(d['end_time']),timezone=d.get('timezone','UTC'),
                 attendees=d.get('attendees'),description=d.get('description'),created_by=current_user.id)
        db.session.add(ev); db.session.commit()
        return jsonify({'id':ev.id,'title':ev.title}),201
    except Exception as ex: return jsonify({'error':str(ex)}),400

@app.route('/api/allocations', methods=['GET'])
@login_required
def api_allocations():
    return jsonify([{'id':a.id,'event_id':a.event_id,'resource_id':a.resource_id,'quantity_used':a.quantity_used}
                    for a in Allocation.query.all()])

@app.route('/api/allocations', methods=['POST'])
@login_required
def api_create_alloc():
    if not current_user.can_edit(): return jsonify({'error':'Forbidden'}),403
    d=request.get_json(); eid=d.get('event_id'); rid=d.get('resource_id'); qty=d.get('quantity_used',1)
    ev=db.session.get(Event,eid)
    if not ev: return jsonify({'error':'Event not found'}),404
    c=check_conflicts(rid,ev.start_time,ev.end_time,qty_needed=qty)
    if c: return jsonify({'error':'Conflict','conflicts':[x['reason'] for x in c]}),409
    a=Allocation(event_id=eid,resource_id=rid,quantity_used=qty)
    db.session.add(a); db.session.commit()
    return jsonify({'id':a.id}),201

@app.route('/api/resources', methods=['GET'])
@login_required
def api_resources():
    return jsonify([{'id':r.id,'name':r.name,'type':r.type,'capacity':r.capacity,'quantity':r.quantity}
                    for r in Resource.query.filter_by(is_active=True).all()])

@app.route('/api/check-conflict', methods=['POST'])
@login_required
def api_check_conflict():
    d=request.get_json(); eid=d.get('event_id'); rid=d.get('resource_id'); qty=d.get('quantity',1)
    ev=db.session.get(Event,eid)
    if not ev: return jsonify({'error':'Event not found'}),404
    c=check_conflicts(rid,ev.start_time,ev.end_time,qty_needed=qty)+check_capacity(rid,ev.attendees)
    return jsonify({'has_conflict':bool(c),'conflicts':[x['reason'] for x in c]})


@app.route('/api/events/<int:eid>', methods=['GET'])
@login_required
def api_event_detail(eid):
    e = db.session.get(Event, eid)
    if not e or not e.is_active: return jsonify({'error':'Not found'}),404
    return jsonify({'id':e.id,'title':e.title,'description':e.description,
                    'start_time':e.start_time.isoformat(),'end_time':e.end_time.isoformat(),
                    'timezone':e.timezone,'attendees':e.attendees,
                    'resources':[{'id':a.resource_id,'name':a.resource.name} for a in e.allocations]})

@app.route('/api/events/<int:eid>', methods=['PUT'])
@login_required
def api_update_event(eid):
    if not current_user.can_edit(): return jsonify({'error':'Forbidden'}),403
    e = db.session.get(Event, eid)
    if not e or not e.is_active: return jsonify({'error':'Not found'}),404
    d = request.get_json()
    try:
        if 'title'       in d: e.title       = d['title']
        if 'description' in d: e.description = d['description']
        if 'start_time'  in d: e.start_time  = datetime.fromisoformat(d['start_time'])
        if 'end_time'    in d: e.end_time    = datetime.fromisoformat(d['end_time'])
        if 'timezone'    in d: e.timezone    = d['timezone']
        if 'attendees'   in d: e.attendees   = d['attendees']
        db.session.commit()
        return jsonify({'id':e.id,'title':e.title})
    except Exception as ex: return jsonify({'error':str(ex)}),400

@app.route('/api/events/<int:eid>', methods=['DELETE'])
@login_required
def api_delete_event(eid):
    if not current_user.can_edit(): return jsonify({'error':'Forbidden'}),403
    e = db.session.get(Event, eid)
    if not e or not e.is_active: return jsonify({'error':'Not found'}),404
    e.is_active = False; db.session.commit()
    return jsonify({'deleted':True})

@app.route('/api/resources/<int:rid>', methods=['GET'])
@login_required
def api_resource_detail(rid):
    r = db.session.get(Resource, rid)
    if not r or not r.is_active: return jsonify({'error':'Not found'}),404
    return jsonify({'id':r.id,'name':r.name,'type':r.type,
                    'capacity':r.capacity,'quantity':r.quantity,'description':r.description})

@app.route('/api/resources/<int:rid>', methods=['PUT'])
@login_required
def api_update_resource(rid):
    if not current_user.can_edit(): return jsonify({'error':'Forbidden'}),403
    r = db.session.get(Resource, rid)
    if not r or not r.is_active: return jsonify({'error':'Not found'}),404
    d = request.get_json()
    if 'name'        in d: r.name        = d['name']
    if 'type'        in d: r.type        = d['type']
    if 'capacity'    in d: r.capacity    = d['capacity']
    if 'quantity'    in d: r.quantity    = d['quantity']
    if 'description' in d: r.description = d['description']
    db.session.commit()
    return jsonify({'id':r.id,'name':r.name})

@app.route('/api/resources/<int:rid>', methods=['DELETE'])
@login_required
def api_delete_resource(rid):
    if not current_user.is_admin(): return jsonify({'error':'Forbidden'}),403
    r = db.session.get(Resource, rid)
    if not r or not r.is_active: return jsonify({'error':'Not found'}),404
    r.is_active = False; db.session.commit()
    return jsonify({'deleted':True})

@app.route('/api/allocations/<int:aid>', methods=['DELETE'])
@login_required
def api_delete_alloc(aid):
    if not current_user.can_edit(): return jsonify({'error':'Forbidden'}),403
    a = db.session.get(Allocation, aid)
    if not a: return jsonify({'error':'Not found'}),404
    db.session.delete(a); db.session.commit()
    return jsonify({'deleted':True})

# ── Seed ──────────────────────────────────────────────────────────────────────

def seed():
    if User.query.filter_by(username='admin').first():
        return
    admin=User(username='admin',    email='admin@aerele.in', role='admin',     active=True)
    org  =User(username='organizer',email='org@aerele.in',   role='organizer', active=True)
    view =User(username='viewer',   email='viewer@aerele.in',role='viewer',    active=True)
    admin.set_password('admin123'); org.set_password('org123'); view.set_password('view123')
    for u in [admin,org,view]: db.session.add(u)

    ha=Resource(name='Conference Hall A',type='room',      capacity=50)
    hb=Resource(name='Training Room B',  type='room',      capacity=20)
    lab=Resource(name='Computer Lab',    type='room',      capacity=30)
    pk=Resource(name='Dr. Priya Kumar',  type='instructor')
    ah=Resource(name='Mr. Ali Hassan',   type='instructor')
    pr=Resource(name='Projector',        type='equipment', quantity=3)
    lp=Resource(name='Laptop',           type='equipment', quantity=10)
    mc=Resource(name='Wireless Mic',     type='equipment', quantity=5)
    for r in [ha,hb,lab,pk,ah,pr,lp,mc]: db.session.add(r)
    db.session.commit()

    base=datetime.now().replace(hour=9,minute=0,second=0,microsecond=0)
    def mke(t,d,sh,dur,att):
        s=base+timedelta(days=d,hours=sh-9)
        return Event(title=t,start_time=s,end_time=s+timedelta(hours=dur),
                     timezone='Asia/Kolkata',attendees=att,created_by=admin.id)
    e1=mke('Python Bootcamp Day 1',0,9,4,25)
    e2=mke('Python Bootcamp Day 2',1,9,4,25)
    e3=mke('DevOps Workshop',0,14,3,18)
    e4=mke('Q3 Strategy Meeting',2,9,2,10)
    e5=mke('Flask API Masterclass',3,9,4,22)
    for e in [e1,e2,e3,e4,e5]: db.session.add(e)
    db.session.commit()

    for al in [
        Allocation(event_id=e1.id,resource_id=lab.id,quantity_used=1),
        Allocation(event_id=e1.id,resource_id=pk.id, quantity_used=1),
        Allocation(event_id=e1.id,resource_id=lp.id, quantity_used=5),
        Allocation(event_id=e2.id,resource_id=lab.id,quantity_used=1),
        Allocation(event_id=e2.id,resource_id=pk.id, quantity_used=1),
        Allocation(event_id=e3.id,resource_id=hb.id, quantity_used=1),
        Allocation(event_id=e3.id,resource_id=ah.id, quantity_used=1),
        Allocation(event_id=e3.id,resource_id=pr.id, quantity_used=1),
        Allocation(event_id=e4.id,resource_id=hb.id, quantity_used=1),
        Allocation(event_id=e5.id,resource_id=lab.id,quantity_used=1),
        Allocation(event_id=e5.id,resource_id=pk.id, quantity_used=1),
    ]: db.session.add(al)
    db.session.commit()
    print('✅  Seeded: admin/admin123  organizer/org123  viewer/view123')

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        # Auto-fix: detect old schema and recreate DB if needed
        needs_reset = False
        try:
            from sqlalchemy import inspect as sa_inspect
            inspector = sa_inspect(db.engine)
            tables = inspector.get_table_names()
            if 'users' not in tables:
                needs_reset = True
            else:
                cols = [c['name'] for c in inspector.get_columns('users')]
                if 'active' not in cols:
                    print('Old DB schema detected. Recreating...')
                    needs_reset = True
        except Exception as ex:
            print(f'DB check error: {ex}')
            needs_reset = True

        if needs_reset:
            db.drop_all()
            print('Old tables dropped.')

        db.create_all()
        seed()
        print('DB ready.')

    app.run(debug=True, port=5000, use_reloader=False)
