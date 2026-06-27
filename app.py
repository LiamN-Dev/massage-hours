import os
import logging
from flask import Flask, render_template, request, redirect, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "massage_debt_secret_key_0831")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///massages.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

class User(db.Model):
    __tablename__ = "user"
    id        = db.Column(db.Integer, primary_key=True)
    username  = db.Column(db.String(50), unique=True, nullable=False)
    password  = db.Column(db.String(255), nullable=False)
    name      = db.Column(db.String(50), nullable=False)
    role      = db.Column(db.String(20), default="user")
    is_locked = db.Column(db.Boolean, default=False)

class GlobalPool(db.Model):
    __tablename__ = "global_pool"
    id              = db.Column(db.Integer, primary_key=True)
    balance_minutes = db.Column(db.Integer, default=1800)

class TimeSlot(db.Model):
    __tablename__ = "time_slot"
    id                 = db.Column(db.Integer, primary_key=True)
    slot_type          = db.Column(db.String(20), nullable=False)
    date               = db.Column(db.String(20), nullable=False)
    start_time         = db.Column(db.String(10), nullable=False)
    end_time           = db.Column(db.String(10), nullable=True)
    duration_minutes   = db.Column(db.Integer, nullable=True)
    status             = db.Column(db.String(20), default="available")
    claimed_by         = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    requested_duration = db.Column(db.Integer, nullable=True)

class Notification(db.Model):
    __tablename__ = "notification"
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    is_global = db.Column(db.Boolean, default=False)
    message   = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Receipt(db.Model):
    __tablename__ = "receipt"
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    user_name       = db.Column(db.String(50), nullable=False)
    description     = db.Column(db.String(200), nullable=False)
    minutes_changed = db.Column(db.Integer, nullable=False)
    timestamp       = db.Column(db.DateTime, default=datetime.utcnow)

def time_to_minutes(t):
    if not t:
        return 0
    h, m = map(int, t.split(":"))
    return h * 60 + m

def minutes_to_time(mins):
    mins = int(mins) % 1440
    return f"{mins // 60:02d}:{mins % 60:02d}"

def get_or_create_pool():
    pool = db.session.execute(select(GlobalPool)).scalar_one_or_none()
    if not pool:
        pool = GlobalPool(balance_minutes=1800)
        db.session.add(pool)
        db.session.commit()
    return pool

def fmt_minutes(total):
    if total is None:
        return "0h 0m"
    sign  = "-" if total < 0 else ""
    total = abs(int(total))
    return f"{sign}{total // 60}h {total % 60}m"

def fmt_ampm(t):
    if not t:
        return ""
    try:
        return datetime.strptime(t.strip(), "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return t

app.jinja_env.filters["format_time"] = fmt_minutes
app.jinja_env.filters["ampm"]        = fmt_ampm

@app.route("/")
def home():
    if "user_id" in session:
        return redirect("/secret-portal-0831" if session["role"] == "admin" else "/dashboard")
    return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username   = request.form["username"].strip().lower()
        password   = request.form["password"].strip()
        admin_pass = os.environ.get("ADMIN_PASSWORD", "08310831")
        if username == "admin" and password == admin_pass:
            session.update(user_id=0, username="admin", role="admin")
            return redirect("/secret-portal-0831")
        user = db.session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user and check_password_hash(user.password, password):
            session.update(user_id=user.id, username=user.username, role=user.role)
            return redirect("/dashboard")
        flash("Invalid login credentials.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session or session["role"] != "user":
        return redirect("/login")
    user = db.session.get(User, session["user_id"])
    if not user:
        session.clear()
        return redirect("/login")
    pool            = get_or_create_pool()
    available_slots = db.session.execute(select(TimeSlot).where(TimeSlot.status == "available")).scalars().all()
    my_appointments = db.session.execute(select(TimeSlot).where(TimeSlot.claimed_by == user.id)).scalars().all()
    receipts        = db.session.execute(select(Receipt).where(Receipt.user_id == user.id).order_by(Receipt.timestamp.desc())).scalars().all()
    notifications   = db.session.execute(
        select(Notification)
        .where((Notification.user_id == user.id) | (Notification.is_global == True))
        .order_by(Notification.timestamp.desc())
    ).scalars().all()
    all_approved = db.session.execute(
        select(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).where(TimeSlot.status == "approved")
    ).all()
    return render_template("dashboard.html", user=user, pool=pool,
        available_slots=available_slots, my_appointments=my_appointments,
        notifications=notifications, receipts=receipts, all_approved=all_approved)

@app.route("/book-slot/<int:slot_id>", methods=["POST"])
def book_slot(slot_id):
    if "user_id" not in session or session["role"] != "user":
        return redirect("/login")
    user = db.session.get(User, session["user_id"])
    if not user:
        session.clear()
        return redirect("/login")
    if user.is_locked:
        flash("Your account is locked pending review.")
        return redirect("/dashboard")
    slot = db.session.get(TimeSlot, slot_id)
    if not slot or slot.status != "available":
        flash("Slot no longer available.")
        return redirect("/dashboard")
    if slot.slot_type == "window":
        def to_mins(h, m, ampm):
            h = int(h)
            m = int(m)
            if h == 12: h = 0
            if ampm == "PM": h += 12
            return h * 60 + m
        user_start   = to_mins(request.form.get("start_h", 12), request.form.get("start_m", 0), request.form.get("start_ampm", "AM"))
        user_end     = to_mins(request.form.get("end_h", 12),   request.form.get("end_m", 0),   request.form.get("end_ampm", "AM"))
        req_duration = user_end - user_start
        if req_duration < 0: req_duration += 1440
        if req_duration <= 0 or req_duration > 80:
            flash("Invalid selection (max 1h 20m).")
            return redirect("/dashboard")
        window_start = time_to_minutes(slot.start_time)
        window_end   = time_to_minutes(slot.end_time) if slot.end_time else window_start
        if window_end <= window_start: window_end += 1440
        adj_start = user_start + 1440 if (user_start < window_start and window_end > 1440) else user_start
        adj_end   = adj_start + req_duration
        if adj_start < window_start or adj_end > window_end:
            flash("Selection falls outside window boundaries.")
            return redirect("/dashboard")
        if adj_start > window_start:
            db.session.add(TimeSlot(slot_type="window", date=slot.date,
                start_time=minutes_to_time(window_start), end_time=minutes_to_time(adj_start), status="available"))
        if adj_end < window_end:
            db.session.add(TimeSlot(slot_type="window", date=slot.date,
                start_time=minutes_to_time(adj_end), end_time=minutes_to_time(window_end), status="available"))
        slot.requested_duration = req_duration
        slot.start_time         = minutes_to_time(user_start)
        slot.end_time           = minutes_to_time(user_end)
    else:
        slot.requested_duration = slot.duration_minutes
    slot.claimed_by = user.id
    slot.status     = "pending"
    user.is_locked  = True
    db.session.commit()
    flash("Request submitted! Account locked until admin decision.")
    return redirect("/dashboard")

@app.route("/request-refund/<int:slot_id>", methods=["POST"])
def request_refund(slot_id):
    if "user_id" not in session or session["role"] != "user":
        return redirect("/login")
    user = db.session.get(User, session["user_id"])
    if not user:
        session.clear()
        return redirect("/login")
    slot = db.session.get(TimeSlot, slot_id)
    if slot and slot.claimed_by == user.id and slot.status == "approved":
        slot.status    = "refund_requested"
        user.is_locked = True
        db.session.commit()
        flash("Cancellation pending admin authorization.")
    return redirect("/dashboard")

def require_admin():
    if "user_id" not in session or session["role"] != "admin":
        abort(403)

@app.route("/secret-portal-0831")
def admin_dashboard():
    if "user_id" not in session or session["role"] != "admin":
        abort(404)
    pool  = get_or_create_pool()
    users = db.session.execute(select(User).where(User.role == "user")).scalars().all()
    pending_slots   = db.session.execute(
        select(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).where(TimeSlot.status == "pending")).all()
    refund_requests = db.session.execute(
        select(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).where(TimeSlot.status == "refund_requested")).all()
    all_slots       = db.session.execute(
        select(TimeSlot, User).outerjoin(User, TimeSlot.claimed_by == User.id)).all()
    return render_template("admin.html", pool=pool, users=users,
        pending_slots=pending_slots, refund_requests=refund_requests, all_slots=all_slots)

@app.route("/admin/create-slot", methods=["POST"])
def create_slot():
    require_admin()
    slot_type  = request.form.get("slot_type")
    date       = request.form.get("date")
    start_time = request.form.get("start_time")
    slot = TimeSlot(slot_type=slot_type, date=date, start_time=start_time, status="available")
    if slot_type == "specific":
        slot.duration_minutes = int(request.form.get("spec_hours") or 0) * 60 + int(request.form.get("spec_mins") or 0)
        slot.end_time         = minutes_to_time(time_to_minutes(start_time) + slot.duration_minutes)
    else:
        slot.end_time = request.form.get("end_time")
    db.session.add(slot)
    db.session.commit()
    flash("Slot created.")
    return redirect("/secret-portal-0831")

@app.route("/admin/decide-slot/<int:slot_id>/<string:action>", methods=["POST"])
def decide_slot(slot_id, action):
    require_admin()
    slot = db.session.get(TimeSlot, slot_id)
    if not slot:
        flash("Slot not found.")
        return redirect("/secret-portal-0831")
    user = db.session.get(User, slot.claimed_by) if slot.claimed_by else None
    if action == "approve":
        slot.status = "approved"
        pool = get_or_create_pool()
        if slot.requested_duration:
            pool.balance_minutes -= slot.requested_duration
        if user:
            user.is_locked = False
            if slot.requested_duration:
                db.session.add(Receipt(user_id=user.id, user_name=user.name,
                    description=f"Approved Booking: {slot.date}", minutes_changed=-slot.requested_duration))
            note = request.form.get("admin_message", "").strip()
            db.session.add(Notification(user_id=user.id,
                message=f"Your request for {slot.date} was APPROVED." + (f" {note}" if note else "")))
    elif action == "deny":
        if user:
            user.is_locked = False
            db.session.add(Notification(user_id=user.id, message=f"Your request for {slot.date} was denied."))
        slot.status             = "available"
        slot.claimed_by         = None
        slot.requested_duration = None
    db.session.commit()
    return redirect("/secret-portal-0831")

@app.route("/admin/decide-refund/<int:slot_id>/<string:action>", methods=["POST"])
def decide_refund(slot_id, action):
    require_admin()
    slot = db.session.get(TimeSlot, slot_id)
    if not slot:
        flash("Slot not found.")
        return redirect("/secret-portal-0831")
    user = db.session.get(User, slot.claimed_by) if slot.claimed_by else None
    if action == "approve":
        pool = get_or_create_pool()
        if slot.requested_duration:
            pool.balance_minutes += slot.requested_duration
        if user:
            user.is_locked = False
            if slot.requested_duration:
                db.session.add(Receipt(user_id=user.id, user_name=user.name,
                    description="Cancellation Approved", minutes_changed=slot.requested_duration))
            db.session.add(Notification(user_id=user.id, message="Cancellation confirmed. Time restored."))
        slot.status             = "available"
        slot.claimed_by         = None
        slot.requested_duration = None
    elif action == "deny":
        if user:
            user.is_locked = False
            db.session.add(Notification(user_id=user.id, message="Cancellation request denied."))
        slot.status = "approved"
    db.session.commit()
    return redirect("/secret-portal-0831")

@app.route("/admin/update-balance", methods=["POST"])
def update_balance():
    require_admin()
    pool          = get_or_create_pool()
    mode          = request.form.get("mode")
    input_minutes = int(request.form.get("hours") or 0) * 60 + int(request.form.get("minutes") or 0)
    old_balance   = pool.balance_minutes
    if mode == "set":      pool.balance_minutes  = input_minutes
    elif mode == "add":    pool.balance_minutes += input_minutes
    elif mode == "subtract": pool.balance_minutes -= input_minutes
    db.session.add(Receipt(user_id=None, user_name="Admin",
        description="Manual balance adjustment", minutes_changed=pool.balance_minutes - old_balance))
    db.session.commit()
    flash("Balance updated.")
    return redirect("/secret-portal-0831")

@app.route("/admin/create-user", methods=["POST"])
def create_user():
    require_admin()
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "").strip()
    name     = request.form.get("name", "").strip()
    if not all([username, password, name]):
        flash("All fields are required.")
        return redirect("/secret-portal-0831")
    if db.session.execute(select(User).where(User.username == username)).scalar_one_or_none():
        flash("Username already exists.")
        return redirect("/secret-portal-0831")
    db.session.add(User(username=username, password=generate_password_hash(password), name=name, role="user"))
    db.session.commit()
    flash(f'User "{name}" created.')
    return redirect("/secret-portal-0831")

@app.route("/admin/unlock-user/<int:user_id>", methods=["POST"])
def unlock_user(user_id):
    require_admin()
    user = db.session.get(User, user_id)
    if user:
        user.is_locked = False
        db.session.commit()
        flash(f"{user.name} unlocked.")
    return redirect("/secret-portal-0831")

@app.route("/admin/change-password", methods=["POST"])
def change_password():
    require_admin()
    uid          = request.form.get("user_id", "").strip()
    new_password = request.form.get("new_password", "").strip()
    if not uid or not new_password:
        flash("Missing user or password.")
        return redirect("/secret-portal-0831")
    user = db.session.get(User, int(uid))
    if user:
        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash(f"Password updated for {user.name}.")
    return redirect("/secret-portal-0831")

@app.route("/admin/send-notification", methods=["POST"])
def send_notification():
    require_admin()
    target = request.form.get("target")
    msg    = request.form.get("message", "").strip()
    if msg:
        if target == "global":
            db.session.add(Notification(is_global=True, message=msg))
        else:
            db.session.add(Notification(user_id=int(target), message=msg))
        db.session.commit()
        flash("Notification sent.")
    return redirect("/secret-portal-0831")

@app.route("/admin/delete-slot/<int:slot_id>", methods=["POST"])
def delete_slot(slot_id):
    require_admin()
    slot = db.session.get(TimeSlot, slot_id)
    if slot:
        db.session.delete(slot)
        db.session.commit()
        flash("Slot deleted.")
    return redirect("/secret-portal-0831")

with app.app_context():
    try:
        db.create_all()
        get_or_create_pool()
        logger.info("DB ready.")
    except Exception as e:
        logger.error(f"Startup DB error: {e}")
        raise

if __name__ == "__main__":
    app.run(debug=True)
