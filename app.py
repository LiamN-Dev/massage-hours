import os
from flask import Flask, render_template, request, redirect, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "massage_debt_secret_key_0831")

# --- DATABASE SETUP ---
# On Render: set DATABASE_URL env var to your PostgreSQL connection string.
# Data will persist across restarts as long as you use PostgreSQL.
# The sqlite fallback is only for local development — it will reset on Render deploys.
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///massages.db')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- DATABASE MODELS ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), default='user')
    is_locked = db.Column(db.Boolean, default=False)

class GlobalPool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    balance_minutes = db.Column(db.Integer, default=1800)

class TimeSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slot_type = db.Column(db.String(20), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    start_time = db.Column(db.String(10), nullable=False)
    end_time = db.Column(db.String(10), nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='available')
    claimed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    requested_duration = db.Column(db.Integer, nullable=True)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    is_global = db.Column(db.Boolean, default=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# FIX: Receipt.user_id is now nullable so admin receipts (no real user) don't cause
# a foreign key integrity error when user_id=0 (which doesn't exist in the DB).
class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user_name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    minutes_changed = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# --- TIME UTILITIES ---

def time_to_minutes(t_str):
    if not t_str:
        return 0
    h, m = map(int, t_str.split(':'))
    return h * 60 + m

def minutes_to_time(mins):
    mins = mins % (24 * 60)
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"

# --- JINJA FILTERS ---

def format_minutes(total_minutes):
    if total_minutes is None:
        return "0h 0m"
    hours = abs(total_minutes) // 60
    minutes = abs(total_minutes) % 60
    sign = "-" if total_minutes < 0 else ""
    return f"{sign}{hours}h {minutes}m"

def format_ampm(time_str):
    if not time_str:
        return ""
    try:
        t = datetime.strptime(time_str.strip(), "%H:%M")
        return t.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return time_str

app.jinja_env.filters['format_time'] = format_minutes
app.jinja_env.filters['ampm'] = format_ampm

# --- POOL HELPER ---

def get_or_create_pool():
    pool = GlobalPool.query.first()
    if not pool:
        pool = GlobalPool(balance_minutes=1800)
        db.session.add(pool)
        db.session.commit()
    return pool

# --- CORE ROUTING ---

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect('/secret-portal-0831' if session['role'] == 'admin' else '/dashboard')
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password'].strip()

        # Hardcoded admin fallback (no DB row needed)
        env_admin_pass = os.environ.get("ADMIN_PASSWORD", "08310831")
        if username == 'admin' and password == env_admin_pass:
            session['user_id'] = 0
            session['username'] = 'admin'
            session['role'] = 'admin'
            return redirect('/secret-portal-0831')

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            return redirect('/dashboard')

        flash('Invalid login credentials.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- USER DASHBOARD ---

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')

    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect('/login')

    pool = get_or_create_pool()
    available_slots = TimeSlot.query.filter_by(status='available').all()
    my_appointments = TimeSlot.query.filter_by(claimed_by=user.id).all()

    notifications = Notification.query.filter(
        (Notification.user_id == user.id) | (Notification.is_global == True)
    ).order_by(Notification.timestamp.desc()).all()

    receipts = Receipt.query.filter_by(user_id=user.id).order_by(Receipt.timestamp.desc()).all()

    # FIX: Use db.session.execute + db.select instead of the legacy Query API join,
    # which can behave inconsistently across SQLAlchemy versions on Render.
    stmt = (
        db.select(TimeSlot, User)
        .join(User, TimeSlot.claimed_by == User.id)
        .where(TimeSlot.status == 'approved')
    )
    all_approved = db.session.execute(stmt).all()

    return render_template('dashboard.html', user=user, pool=pool,
                           available_slots=available_slots,
                           my_appointments=my_appointments,
                           notifications=notifications,
                           receipts=receipts,
                           all_approved=all_approved)

@app.route('/book-slot/<int:slot_id>', methods=['POST'])
def book_slot(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')

    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect('/login')

    if user.is_locked:
        flash('Your account is locked pending review.')
        return redirect('/dashboard')

    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.status != 'available':
        flash('Slot no longer available.')
        return redirect('/dashboard')

    if slot.slot_type == 'window':
        st_h   = int(request.form.get('start_h', 12))
        st_m   = int(request.form.get('start_m', 0))
        st_ap  = request.form.get('start_ampm', 'AM')
        en_h   = int(request.form.get('end_h', 12))
        en_m   = int(request.form.get('end_m', 0))
        en_ap  = request.form.get('end_ampm', 'AM')

        def to_mins(h, m, ampm):
            if h == 12:
                h = 0
            if ampm == 'PM':
                h += 12
            return (h * 60) + m

        user_start = to_mins(st_h, st_m, st_ap)
        user_end   = to_mins(en_h, en_m, en_ap)

        req_duration = user_end - user_start
        if req_duration < 0:
            req_duration += (24 * 60)

        if req_duration <= 0 or req_duration > 80:
            flash('Invalid selection (Max booking allowed: 1h 20m).')
            return redirect('/dashboard')

        window_start = time_to_minutes(slot.start_time)
        window_end   = time_to_minutes(slot.end_time) if slot.end_time else window_start

        if window_end <= window_start:
            window_end += (24 * 60)

        adj_user_start = user_start + (24 * 60) if (user_start < window_start and window_end > (24 * 60)) else user_start
        adj_user_end   = adj_user_start + req_duration

        if adj_user_start < window_start or adj_user_end > window_end:
            flash('Error: Selection drops outside window range boundaries.')
            return redirect('/dashboard')

        if adj_user_start > window_start:
            db.session.add(TimeSlot(
                slot_type='window', date=slot.date,
                start_time=minutes_to_time(window_start),
                end_time=minutes_to_time(adj_user_start),
                status='available'
            ))
        if adj_user_end < window_end:
            db.session.add(TimeSlot(
                slot_type='window', date=slot.date,
                start_time=minutes_to_time(adj_user_end),
                end_time=minutes_to_time(window_end),
                status='available'
            ))

        slot.requested_duration = req_duration
        slot.start_time = minutes_to_time(user_start)
        slot.end_time   = minutes_to_time(user_end)
    else:
        slot.requested_duration = slot.duration_minutes

    slot.claimed_by = user.id
    slot.status     = 'pending'
    user.is_locked  = True
    db.session.commit()
    flash('Request submitted! Account locked until admin decision.')
    return redirect('/dashboard')

@app.route('/request-refund/<int:slot_id>', methods=['POST'])
def request_refund(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')

    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect('/login')

    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.claimed_by == user.id and slot.status == 'approved':
        slot.status    = 'refund_requested'
        user.is_locked = True
        db.session.commit()
        flash('Cancellation pending admin authorization.')
    return redirect('/dashboard')

# --- ADMIN PANEL ---

@app.route('/secret-portal-0831')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(404)

    pool  = get_or_create_pool()
    users = User.query.filter_by(role='user').all()

    # FIX: Use modern SQLAlchemy select() API — avoids 500s on Render's PostgreSQL
    pending_stmt = (
        db.select(TimeSlot, User)
        .join(User, TimeSlot.claimed_by == User.id)
        .where(TimeSlot.status == 'pending')
    )
    pending_slots = db.session.execute(pending_stmt).all()

    refund_stmt = (
        db.select(TimeSlot, User)
        .join(User, TimeSlot.claimed_by == User.id)
        .where(TimeSlot.status == 'refund_requested')
    )
    refund_requests = db.session.execute(refund_stmt).all()

    all_stmt = (
        db.select(TimeSlot, User)
        .outerjoin(User, TimeSlot.claimed_by == User.id)
    )
    all_slots = db.session.execute(all_stmt).all()

    return render_template('admin.html', pool=pool, users=users,
                           pending_slots=pending_slots,
                           refund_requests=refund_requests,
                           all_slots=all_slots)

@app.route('/admin/create-slot', methods=['POST'])
def create_slot():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    slot_type  = request.form.get('slot_type')
    date       = request.form.get('date')
    start_time = request.form.get('start_time')
    new_slot   = TimeSlot(slot_type=slot_type, date=date, start_time=start_time, status='available')

    if slot_type == 'specific':
        new_slot.duration_minutes = (int(request.form.get('spec_hours') or 0) * 60) + int(request.form.get('spec_mins') or 0)
        new_slot.end_time = minutes_to_time(time_to_minutes(start_time) + new_slot.duration_minutes)
    else:
        new_slot.end_time = request.form.get('end_time')

    db.session.add(new_slot)
    db.session.commit()
    flash('Slot created successfully.')
    return redirect('/secret-portal-0831')

@app.route('/admin/decide-slot/<int:slot_id>/<string:action>', methods=['POST'])
def decide_slot(slot_id, action):
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    slot = TimeSlot.query.get_or_404(slot_id)

    # FIX: Guard against missing user (e.g. slot with no claimed_by, or deleted user)
    user = User.query.get(slot.claimed_by) if slot.claimed_by else None

    if action == 'approve':
        slot.status = 'approved'
        pool = get_or_create_pool()
        if slot.requested_duration:
            pool.balance_minutes -= slot.requested_duration
            # FIX: Only create a receipt if there's an actual user to attach it to
            if user:
                db.session.add(Receipt(
                    user_id=user.id,
                    user_name=user.name,
                    description=f"Approved Booking: {slot.date}",
                    minutes_changed=-slot.requested_duration
                ))
        if user:
            user.is_locked = False
            admin_msg = request.form.get('admin_message', '').strip()
            note = f" Note: {admin_msg}" if admin_msg else ""
            db.session.add(Notification(
                user_id=user.id,
                message=f"Your request for {slot.date} was APPROVED.{note}"
            ))

    elif action == 'deny':
        # FIX: Reset the slot back to fully available before unlocking user
        if user:
            user.is_locked = False
            db.session.add(Notification(
                user_id=user.id,
                message=f"Your request for {slot.date} was denied."
            ))
        slot.status           = 'available'
        slot.claimed_by       = None
        slot.requested_duration = None

    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/decide-refund/<int:slot_id>/<string:action>', methods=['POST'])
def decide_refund(slot_id, action):
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    slot = TimeSlot.query.get_or_404(slot_id)

    # FIX: Guard against missing user
    user = User.query.get(slot.claimed_by) if slot.claimed_by else None

    if action == 'approve':
        pool = get_or_create_pool()
        if slot.requested_duration:
            pool.balance_minutes += slot.requested_duration
            if user:
                db.session.add(Receipt(
                    user_id=user.id,
                    user_name=user.name,
                    description="Cancellation Approved",
                    minutes_changed=slot.requested_duration
                ))
        if user:
            user.is_locked = False
            db.session.add(Notification(user_id=user.id, message="Cancellation confirmed. Time restored."))

        slot.status           = 'available'
        slot.claimed_by       = None
        slot.requested_duration = None

    elif action == 'deny':
        if user:
            user.is_locked = False
            db.session.add(Notification(user_id=user.id, message="Cancellation request denied."))
        slot.status = 'approved'

    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/update-balance', methods=['POST'])
def update_balance():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    pool         = get_or_create_pool()
    mode         = request.form.get('mode')
    input_minutes = (int(request.form.get('hours') or 0) * 60) + int(request.form.get('minutes') or 0)
    old_balance  = pool.balance_minutes

    if mode == 'set':
        pool.balance_minutes = input_minutes
    elif mode == 'add':
        pool.balance_minutes += input_minutes
    elif mode == 'subtract':
        pool.balance_minutes -= input_minutes

    # FIX: user_id=None (nullable) instead of user_id=0 which breaks the FK constraint
    db.session.add(Receipt(
        user_id=None,
        user_name="Admin",
        description="Manual balance adjustment",
        minutes_changed=pool.balance_minutes - old_balance
    ))
    db.session.commit()
    flash('Balance updated.')
    return redirect('/secret-portal-0831')

@app.route('/admin/create-user', methods=['POST'])
def create_user():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    username = request.form.get('username', '').strip().lower()
    password = request.form.get('password', '').strip()
    name     = request.form.get('name', '').strip()

    if not username or not password or not name:
        flash('All fields are required.')
        return redirect('/secret-portal-0831')

    if User.query.filter_by(username=username).first():
        flash('Username already exists.')
        return redirect('/secret-portal-0831')

    db.session.add(User(
        username=username,
        password=generate_password_hash(password),
        name=name,
        role='user'
    ))
    db.session.commit()
    flash(f'User "{name}" created successfully.')
    return redirect('/secret-portal-0831')

@app.route('/admin/unlock-user/<int:user_id>', methods=['POST'])
def unlock_user(user_id):
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    user = User.query.get_or_404(user_id)
    user.is_locked = False
    db.session.commit()
    flash(f'{user.name} unlocked.')
    return redirect('/secret-portal-0831')

@app.route('/admin/change-password', methods=['POST'])
def change_password():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    uid          = request.form.get('user_id')
    new_password = request.form.get('new_password', '').strip()

    if not uid or not new_password:
        flash('Missing user ID or password.')
        return redirect('/secret-portal-0831')

    user = User.query.get(int(uid))
    if user:
        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash(f'Password updated for {user.name}.')
    return redirect('/secret-portal-0831')

@app.route('/admin/send-notification', methods=['POST'])
def send_notification():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    target = request.form.get('target')
    msg    = request.form.get('message', '').strip()

    if msg:
        if target == 'global':
            db.session.add(Notification(is_global=True, message=msg))
        else:
            db.session.add(Notification(user_id=int(target), message=msg))
        db.session.commit()
        flash('Notification sent.')
    return redirect('/secret-portal-0831')

@app.route('/admin/delete-slot/<int:slot_id>', methods=['POST'])
def delete_slot(slot_id):
    if 'user_id' not in session or session['role'] != 'admin':
        abort(403)

    slot = TimeSlot.query.get_or_404(slot_id)
    db.session.delete(slot)
    db.session.commit()
    flash('Slot deleted.')
    return redirect('/secret-portal-0831')

# --- STARTUP: Create tables and seed pool ---
# This runs inside Gunicorn on Render so tables always exist on first boot.
with app.app_context():
    db.create_all()
    get_or_create_pool()

if __name__ == '__main__':
    app.run(debug=True)
