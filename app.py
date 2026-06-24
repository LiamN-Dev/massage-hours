import os
from flask import Flask, render_template, request, redirect, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
app.secret_key = "massage_debt_secret_key_0831"
app.config['SESSION_PERMANENT'] = False

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
    password = db.Column(db.String(50), nullable=False) 
    name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), default='user')
    is_locked = db.Column(db.Boolean, default=False) # Restored missing column

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
        
        if username == 'admin' and password == '08310831':
            session['user_id'] = 0
            session['username'] = 'admin'
            session['role'] = 'admin'
            return redirect('/secret-portal-0831')
            
        user = User.query.filter_by(username=username, password=password).first()
        if user:
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
        
    pool = GlobalPool.query.first()
    
    available_slots = TimeSlot.query.filter_by(status='available').all()
    my_appointments = TimeSlot.query.filter_by(claimed_by=user.id).all()
    
    notifications = Notification.query.filter(
        (Notification.user_id == user.id) | (Notification.is_global == True)
    ).order_by(Notification.timestamp.desc()).all()
    
    receipts = Receipt.query.order_by(Receipt.timestamp.desc()).all()
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
        flash('Booking rejected. Your profile is locked pending active operation approvals.')
        return redirect('/dashboard')
        
    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.status != 'available':
        flash('Slot already locked.')
        return redirect('/dashboard')
        
    slot.claimed_by = session['user_id']
    slot.status = 'pending'
    
    if slot.slot_type == 'window':
        hours = int(request.form.get('hours', 0))
        minutes = int(request.form.get('minutes', 0))
        slot.requested_duration = (hours * 60) + minutes
    else:
        slot.requested_duration = slot.duration_minutes
        
    user.is_locked = True
    db.session.commit()
    flash('Booking request route dispatched to admin panel.')
    return redirect('/dashboard')

@app.route('/request-refund/<int:slot_id>', methods=['POST'])
def request_refund(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    user = User.query.get(session['user_id'])
    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.claimed_by == session['user_id'] and slot.status == 'approved':
        slot.status = 'refund_requested'
        user.is_locked = True
        db.session.commit()
        flash('Cancellation filed for review.')
    return redirect('/dashboard')

# --- ADMIN COMMAND ROUTER ---

@app.route('/secret-portal-0831')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(404)
        
    pool = GlobalPool.query.first()
    users = User.query.filter_by(role='user').all()
    pending_slots = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'pending').all()
    refund_requests = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'refund_requested').all()
    
    # Restored correct outerjoin query to prevent looping crashes
    all_slots = db.session.query(TimeSlot, User).outerjoin(User, TimeSlot.claimed_by == User.id).all()
    
    return render_template('admin.html', pool=pool, users=users, pending_slots=pending_slots, refund_requests=refund_requests, all_slots=all_slots)

@app.route('/admin/create-slot', methods=['POST'])
def create_slot():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
        
    slot_type = request.form.get('slot_type')
    date = request.form.get('date')
    start_time = request.form.get('start_time')
    
    new_slot = TimeSlot(
        slot_type=slot_type,
        date=date,
        start_time=start_time,
        status='available'
    )
    
    if slot_type == 'specific':
        # Restored correct form mapping names
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
        pool = GlobalPool.query.first()
        
        if pool and slot.requested_duration:
            pool.balance_minutes -= slot.requested_duration
            receipt = Receipt(
                user_id=user.id,
                user_name=user.name,
                description=f"Confirmed Booking: {slot.date} @ {slot.start_time}",
                minutes_changed=-slot.requested_duration
            )
            db.session.add(receipt)
            
        if user:
            user.is_locked = True
            
        notif = Notification(user_id=user.id, is_global=False, message=f"Your booking for {slot.date} has been APPROVED. {admin_message}")
        db.session.add(notif)
        flash('Booking validated successfully.')
        
    elif action == 'deny':
        if user:
            user.is_locked = False
            
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
        pool = GlobalPool.query.first()
        if pool and slot.requested_duration:
            pool.balance_minutes += slot.requested_duration
            receipt = Receipt(
                user_id=user.id,
                user_name=user.name,
                description=f"Cancellation Approved: Returned hours for {slot.date}",
                minutes_changed=slot.requested_duration
            )
            db.session.add(receipt)
            
        if user:
            user.is_locked = False
            
        notif = Notification(user_id=user.id, is_global=False, message=f"Cancellation complete. Time restored. {admin_message}")
        db.session.add(notif)
        
        slot.status = 'available'
        slot.claimed_by = None
        slot.requested_duration = None
        flash('Cancellation evaluated and balances restored.')
        
    elif action == 'deny':
        if user:
            user.is_locked = True
            
        slot.status = 'approved'
        notif = Notification(user_id=user.id, is_global=False, message=f"Cancellation request declined. {admin_message}")
        db.session.add(notif)
        flash('Cancellation request declined.')
        
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/update-balance', methods=['POST'])
def update_balance():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    pool = GlobalPool.query.first()
    
    mode = request.form.get('mode') 
    hours = int(request.form.get('hours') or 0)
    mins = int(request.form.get('minutes') or 0)
    input_minutes = (hours * 60) + mins
    
    old_balance = pool.balance_minutes
    if mode == 'set':
        pool.balance_minutes = input_minutes
    elif mode == 'add':
        pool.balance_minutes += input_minutes
    elif mode == 'subtract':
        pool.balance_minutes -= input_minutes
        
    diff = pool.balance_minutes - old_balance
    
    receipt = Receipt(user_id=0, user_name="Admin Override", description=f"Manual balance configuration change ({mode.upper()})", minutes_changed=diff)
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
        
    new_user = User(username=username, password=password, name=name, role='user', is_locked=False)
    db.session.add(new_user)
    db.session.commit()
    flash(f'New profile deployed successfully for {name}.')
    return redirect('/secret-portal-0831')

@app.route('/admin/unlock-user/<int:user_id>', methods=['POST'])
def unlock_user(user_id):
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    user = User.query.get_or_404(user_id)
    user.is_locked = False
    db.session.commit()
    flash(f'Account restrictions manually removed for user: {user.name}.')
    return redirect('/secret-portal-0831')

@app.route('/admin/change-password', methods=['POST'])
def change_password():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    target_user_id = int(request.form.get('user_id'))
    new_password = request.form.get('new_password').strip()
    
    user = User.query.get(target_user_id)
    if user:
        user.password = new_password
        db.session.commit()
        flash(f'Access codes modified for user: {user.name}.')
    return redirect('/secret-portal-0831')

@app.route('/admin/send-notification', methods=['POST'])
def send_notification():
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    target = request.form.get('target') 
    message_text = request.form.get('message', '').strip()
    
    if not message_text:
        return redirect('/secret-portal-0831')
        
    if target == 'global':
        notif = Notification(is_global=True, message=message_text)
        db.session.add(notif)
    else:
        user_id = int(target)
        notif = Notification(user_id=user_id, is_global=False, message=message_text)
        db.session.add(notif)
        
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/delete-slot/<int:slot_id>', methods=['POST'])
def delete_slot(slot_id):
    if 'user_id' not in session or session['role'] != 'admin': abort(403)
    slot = TimeSlot.query.get_or_404(slot_id)
    db.session.delete(slot)
    db.session.commit()
    flash('Timeline item safely removed from records.')
    return redirect('/secret-portal-0831')

# --- RENDER DEPLOYMENT CLI COMMANDS ---

@app.cli.command("init-db")
def init_db():
    """Initializes the database schemas and default profiles on Render production setup."""
    db.create_all()
    if not GlobalPool.query.first():
        db.session.add(GlobalPool(balance_minutes=1800))
    if not User.query.filter_by(username='gretta').first():
        db.session.add(User(username='gretta', password='iLOVEpeter10!', name='Gretta', role='user', is_locked=False))
    if not User.query.filter_by(username='peter').first():
        db.session.add(User(username='peter', password='2887', name='Peter', role='user', is_locked=False))
    db.session.commit()
    print("Production database components initialized.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not GlobalPool.query.first():
            db.session.add(GlobalPool(balance_minutes=1800))
        if not User.query.filter_by(username='gretta').first():
            db.session.add(User(username='gretta', password='iLOVEpeter10!', name='Gretta', role='user', is_locked=False))
        if not User.query.filter_by(username='peter').first():
            db.session.add(User(username='peter', password='2887', name='Peter', role='user', is_locked=False))
        db.session.commit()
    app.run(debug=True)
