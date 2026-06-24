import os
from flask import Flask, render_template, request, redirect, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
app.secret_key = "massage_debt_secret_key_0831"

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
    """Converts standard database 24-hour time values (HH:MM) to clean 12-hour AM/PM presentation formats."""
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
    
    # SAFETY CHECK: If the database was reset but the user still has an old browser cookie
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
        
    db.session.commit()
    flash('Booking request route dispatched to admin panel.')
    return redirect('/dashboard')

@app.route('/request-refund/<int:slot_id>', methods=['POST'])
def request_refund(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.claimed_by == session['user_id'] and slot.status == 'approved':
        slot.status = 'refund_requested'
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
    all_slots = TimeSlot.query.all()
    
    return render_template('admin.html', pool=pool, users=users, pending_slots=pending_slots, refund_requests=refund_requests, all_slots=all_slots)

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

# --- RENDER DEPLOYMENT CLI COMMANDS ---

@app.cli.command("init-db")
def init_db():
    """Initializes the database schemas and default profiles on Render production setup."""
    db.create_all()
    if not GlobalPool.query.first():
        db.session.add(GlobalPool(balance_minutes=1800))
    db.session.commit()
    print("Production database components initialized.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not GlobalPool.query.first():
            db.session.add(GlobalPool(balance_minutes=1800))
        db.session.commit()
    app.run(debug=True)
