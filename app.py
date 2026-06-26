import os
from flask import Flask, render_template, request, redirect, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# Secure secret key loaded via environment variables in production
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "massage_debt_secret_key_0831")

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
    password = db.Column(db.String(255), nullable=False) # Increased size for hashes
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

class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user_name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    minutes_changed = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# --- TIME COMPUTATION ENGINE ---

def time_to_minutes(t_str):
    if not t_str: return 0
    h, m = map(int, t_str.split(':'))
    return h * 60 + m

def minutes_to_time(mins):
    mins = mins % (24 * 60)
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"

# --- JINJA CUSTOM INTERFACE FILTERS ---

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

# --- UTILITY HELPERS FOR POOL RESILIENCE ---

def get_or_create_pool():
    pool = GlobalPool.query.first()
    if not pool:
        pool = GlobalPool(balance_minutes=1800)
        db.session.add(pool)
        db.session.commit()
    return pool

# --- NAVIGATION ROUTING ---

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
        
        # Admin credentials fallback to environment variables safely
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
            
        flash('Invalid verification credentials.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- CLIENT DASHBOARD ---

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Your session has expired. Please log in again.')
        return redirect('/login')
        
    pool = get_or_create_pool()
    
    available_slots = TimeSlot.query.filter_by(status='available').all()
    my_appointments = TimeSlot.query.filter_by(claimed_by=user.id).all()
    
    notifications = Notification.query.filter(
        (Notification.user_id == user.id) | (Notification.is_global == True)
    ).order_by(Notification.timestamp.desc()).all()
    
    # Filtered by current user ID to solve the global privacy leak bug
    receipts = Receipt.query.filter_by(user_id=user.id).order_by(Receipt.timestamp.desc()).all()
    all_approved = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'approved').all()

    return render_template('dashboard.html', user=user, pool=pool, available_slots=available_slots, 
                           my_appointments=my_appointments, notifications=notifications, 
                           receipts=receipts, all_approved=all_approved)

@app.route('/book-slot/<int:slot_id>', methods=['POST'])
def book_slot(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    user = User.query.get(session['user_id'])
    if user.is_locked:
        flash('Action Denied: Your account is currently locked.')
        return redirect('/dashboard')
        
    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.status != 'available':
        flash('Slot is no longer available.')
        return redirect('/dashboard')
    
    if slot.slot_type == 'window':
        st_h = int(request.form.get('start_h', 12))
        st_m = int(request.form.get('start_m', 0))
        st_ampm = request.form.get('start_ampm', 'AM')
        
        en_h = int(request.form.get('end_h', 12))
        en_m = int(request.form.get('end_m', 0))
        en_ampm = request.form.get('end_ampm', 'AM')
        
        def to_mins(h, m, ampm):
            if h == 12: h = 0
            if ampm == 'PM': h += 12
            return (h * 60) + m
            
        user_start = to_mins(st_h, st_m, st_ampm)
        user_end = to_mins(en_h, en_m, en_ampm)
        
        req_duration = user_end - user_start
        if req_duration < 0:
            req_duration += (24 * 60)
            
        if req_duration <= 0:
            flash('Please select a valid duration.')
            return redirect('/dashboard')
            
        if req_duration > 80:
            flash('Policy Error: Flexible window requests cannot exceed 1 hour and 20 minutes (80 mins).')
            return redirect('/dashboard')
            
        window_start = time_to_minutes(slot.start_time)
        window_end = time_to_minutes(slot.end_time) if slot.end_time else window_start
        
        # Absolute window tracking to avoid midnight wrap evaluation bugs
        if window_end <= window_start:
            window_end += (24 * 60)
            
        adj_user_start = user_start
        if adj_user_start < window_start and window_end > (24 * 60):
            adj_user_start += (24 * 60)
        adj_user_end = adj_user_start + req_duration
        
        if adj_user_start < window_start or adj_user_end > window_end:
            flash('Error: The time you selected falls outside the available schedule window.')
            return redirect('/dashboard')
            
        if adj_user_start > window_start:
            pre_slot = TimeSlot(
                slot_type='window',
                date=slot.date,
                start_time=minutes_to_time(window_start),
                end_time=minutes_to_time(adj_user_start),
                status='available'
            )
            db.session.add(pre_slot)
            
        if adj_user_end < window_end:
            post_slot = TimeSlot(
                slot_type='window',
                date=slot.date,
                start_time=minutes_to_time(adj_user_end),
                end_time=minutes_to_time(window_end),
                status='available'
            )
            db.session.add(post_slot)
            
        slot.requested_duration = req_duration
        slot.start_time = minutes_to_time(user_start)
        slot.end_time = minutes_to_time(user_end)
    else:
        slot.requested_duration = slot.duration_minutes
        
    slot.claimed_by = int(session['user_id'])
    slot.status = 'pending'
    user.is_locked = True
    
    db.session.commit()
    flash('Booking request submitted! Your account is locked pending admin approval.')
    return redirect('/dashboard')

@app.route('/request-refund/<int:slot_id>', methods=['POST'])
def request_refund(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    user = User.query.get(session['user_id'])
    slot = TimeSlot.query.get_or_404(slot_id)
    # Explictly cast session keys to integer match type checks
    if slot.claimed_by == int(session['user_id']) and slot.status == 'approved':
        slot.status = 'refund_requested'
        user.is_locked = True
        db.session.commit()
        flash('Cancellation filed for review. Account locked pending resolution.')
    return redirect('/dashboard')

# --- ADMIN COMMAND ROUTER ---

@app.route('/secret-portal-0831')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(404)
        
    pool = get_or_create_pool()
    users = User.query.filter_by(role='user').all()
    pending_slots = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'pending').all()
    refund_requests = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'refund_requested').all()
    all_slots = db.session.query(TimeSlot, User).outerjoin(User, TimeSlot.claimed_by == User.id).all()
    
    return render_template('admin.html', pool=pool, users=users, pending_slots=pending_slots, refund_requests=refund_requests, all_slots=all_slots)

@app.route('/admin/create-slot', methods=['POST'])
def create_slot():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
        
    slot_type = request.form.get('slot_type')
    date = request.form.get('date')
    start_time = request.form.get('start_time')
    
    new_slot = TimeSlot(slot_type=slot_type, date=date, start_time=start_time, status='available')
    
    if slot_type == 'specific':
        hours = int(request.form.get('spec_hours') or 0)
        minutes = int(request.form.get('spec_mins') or 0)
        new_slot.duration_minutes = (hours * 60) + minutes
        new_slot.end_time = minutes_to_time(time_to_minutes(start_time) + new_slot.duration_minutes)
    else:
        new_slot.end_time = request.form.get('end_time')
        new_slot.duration_minutes = None
        
    db.session.add(new_slot)
    db.session.commit()
    flash('New operational time slot successfully initialized.')
    return redirect('/secret-portal-0831')

@app.route('/admin/decide-slot/<int:slot_id>/<string:action>', methods=['POST'])
def decide_slot(slot_id, action):
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
        
    slot = TimeSlot.query.get_or_404(slot_id)
    admin_message = request.form.get('admin_message', '').strip()
    user = User.query.get(slot.claimed_by)
    
    if action == 'approve':
        slot.status = 'approved'
        pool = get_or_create_pool()
        if slot.requested_duration:
            pool.balance_minutes -= slot.requested_duration
            receipt = Receipt(user_id=user.id, user_name=user.name, description=f"Confirmed Booking: {slot.date}", minutes_changed=-slot.requested_duration)
            db.session.add(receipt)
        if user: 
            user.is_locked = False # FIX: Unlock user upon active approval state
        notif = Notification(user_id=user.id, is_global=False, message=f"Your booking for {slot.date} has been APPROVED. {admin_message}")
        db.session.add(notif)
        flash('Booking validated successfully.')
        
    elif action == 'deny':
        if user: user.is_locked = False
        notif = Notification(user_id=user.id, is_global=False, message=f"Your booking request for {slot.date} was declined. {admin_message}")
        db.session.add(notif)
        slot.status = 'available'
        slot.claimed_by = None
        slot.requested_duration = None
        flash('Booking request declined and slot safely reclaimed.')
        
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/decide-refund/<int:slot_id>/<string:action>', methods=['POST'])
def decide_refund(slot_id, action):
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
        
    slot = TimeSlot.query.get_or_404(slot_id)
    admin_message = request.form.get('admin_message', '').strip()
    user = User.query.get(slot.claimed_by)
    
    if action == 'approve':
        pool = get_or_create_pool()
        if slot.requested_duration:
            pool.balance_minutes += slot.requested_duration
            receipt = Receipt(user_id=user.id, user_name=user.name, description=f"Cancellation Approved", minutes_changed=slot.requested_duration)
            db.session.add(receipt)
        if user: user.is_locked = False
        notif = Notification(user_id=user.id, is_global=False, message=f"Cancellation complete. Time restored. {admin_message}")
        db.session.add(notif)
        slot.status = 'available'
        slot.claimed_by = None
        slot.requested_duration = None
        flash('Cancellation evaluated and balances restored.')
        
    elif action == 'deny':
        if user: user.is_locked = False # Clear lock status upon evaluation completed
        slot.status = 'approved'
        notif = Notification(user_id=user.id, is_global=False, message=f"Cancellation request declined. {admin_message}")
        db.session.add(notif)
        flash('Cancellation request declined.')
        
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/update-balance', methods=['POST'])
def update_balance():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    pool = get_or_create_pool()
    mode = request.form.get('mode') 
    hours = int(request.form.get('hours') or 0)
    mins = int(request.form.get('minutes') or 0)
    input_minutes = (hours * 60) + mins
    old_balance = pool.balance_minutes
    if mode == 'set': pool.balance_minutes = input_minutes
    elif mode == 'add': pool.balance_minutes += input_minutes
    elif mode == 'subtract': pool.balance_minutes -= input_minutes
    diff = pool.balance_minutes - old_balance
    receipt = Receipt(user_id=0, user_name="Admin Override", description=f"Manual balance adjustment", minutes_changed=diff)
    db.session.add(receipt)
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/create-user', methods=['POST'])
def create_user():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    username = request.form.get('username').strip().lower()
    password = request.form.get('password').strip()
    name = request.form.get('name').strip()
    if User.query.filter_by(username=username).first():
        flash('Error: Profile identifier already taken.')
        return redirect('/secret-portal-0831')
        
    # Use Werkzeug helper for structural hashing securely
    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password=hashed_password, name=name, role='user', is_locked=False)
    db.session.add(new_user)
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/unlock-user/<int:user_id>', methods=['POST'])
def unlock_user(user_id):
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    user = User.query.get_or_404(user_id)
    user.is_locked = False
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/change-password', methods=['POST'])
def change_password():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    target_user_id = int(request.form.get('user_id'))
    new_password = request.form.get('new_password').strip()
    user = User.query.get(target_user_id)
    if user:
        user.password = generate_password_hash(new_password)
        db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/send-notification', methods=['POST'])
def send_notification():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    target = request.form.get('target') 
    message_text = request.form.get('message', '').strip()
    if not message_text: return redirect('/secret-portal-0831')
    if target == 'global': db.session.add(Notification(is_global=True, message=message_text))
    else: db.session.add(Notification(user_id=int(target), is_global=False, message=message_text))
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/delete-slot/<int:slot_id>', methods=['POST'])
def delete_slot(slot_id):
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    db.session.delete(TimeSlot.query.get_or_404(slot_id))
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.cli.command("init-db")
def init_db():
    db.create_all()
    get_or_create_pool()
    print("Database tables initialized successfully.")

# Safe initialization phase for production entry points (Gunicorn context)
with app.app_context():
    db.create_all()
    get_or_create_pool()

if __name__ == '__main__':
    # Local fallback execution thread block
    app.run(debug=True)
