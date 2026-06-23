import os
from flask import Flask, render_template, request, redirect, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
# Secret key to secure sessions/cookies
app.secret_key = "massage_debt_secret_key_0831"

# Database Config: Auto-detects Render's Postgres database, otherwise falls back to local SQLite
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
    password = db.Column(db.String(50), nullable=False) # Plain text as requested
    name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), default='user')     # 'admin' or 'user'
    balance_minutes = db.Column(db.Integer, default=1620) # 27 hours default (27 * 60)

class TimeSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slot_type = db.Column(db.String(20), nullable=False)   # 'specific' or 'window'
    date = db.Column(db.String(20), nullable=False)        # YYYY-MM-DD
    start_time = db.Column(db.String(10), nullable=False)  # HH:MM
    end_time = db.Column(db.String(10), nullable=True)     # Only used for window slots
    duration_minutes = db.Column(db.Integer, nullable=True) # Only used for specific slots
    status = db.Column(db.String(20), default='available') # 'available', 'pending', 'approved', 'refund_requested'
    claimed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    requested_duration = db.Column(db.Integer, nullable=True) # The actual block size claimed

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    minutes_changed = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


# --- CUSTOM JINJA FILTER ---
# Converts total minutes back into a readable "Xh Ym" string for Mom and Dad
def format_minutes(total_minutes):
    if total_minutes is None:
        return "0h 0m"
    hours = abs(total_minutes) // 60
    minutes = abs(total_minutes) % 60
    sign = "-" if total_minutes < 0 else ""
    return f"{sign}{hours}h {minutes}m"

app.jinja_env.filters['format_time'] = format_minutes


# --- BASE NAVIGATION ROUTES ---

@app.route('/')
def home():
    if 'user_id' in session:
        if session['role'] == 'admin':
            return redirect('/secret-portal-0831')
        return redirect('/dashboard')
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password'].strip()
        
        # Hardcoded Admin gate
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
            
        flash('Invalid username or password!')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# --- PARENT CLIENT DASHBOARD ---

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    user = User.query.get(session['user_id'])
    available_slots = TimeSlot.query.filter_by(status='available').all()
    my_appointments = TimeSlot.query.filter_by(claimed_by=user.id).all()
    notifications = Notification.query.filter_by(user_id=user.id).order_by(Notification.timestamp.desc()).all()
    receipts = Receipt.query.filter_by(user_id=user.id).order_by(Receipt.timestamp.desc()).all()
    
    # Grabs confirmed bookings along with the matching user details to display on the public schedule
    all_approved = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'approved').all()

    return render_template('dashboard.html', user=user, available_slots=available_slots, 
                           my_appointments=my_appointments, notifications=notifications, 
                           receipts=receipts, all_approved=all_approved)

@app.route('/book-slot/<int:slot_id>', methods=['POST'])
def book_slot(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.status != 'available':
        flash('This slot is no longer available.')
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
    flash('Appointment submitted for approval!')
    return redirect('/dashboard')

@app.route('/request-refund/<int:slot_id>', methods=['POST'])
def request_refund(slot_id):
    if 'user_id' not in session or session['role'] != 'user':
        return redirect('/login')
        
    slot = TimeSlot.query.get_or_404(slot_id)
    if slot.claimed_by == session['user_id'] and slot.status == 'approved':
        slot.status = 'refund_requested'
        db.session.commit()
        flash('Cancellation request submitted to Admin!')
    return redirect('/dashboard')


# --- HIDDEN ADMIN CONTROL MANAGEMENT ROUTES ---

@app.route('/secret-portal-0831')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'admin':
        abort(404) # Completely hides the route by acting like it doesn't exist
        
    users = User.query.filter_by(role='user').all()
    pending_slots = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'pending').all()
    refund_requests = db.session.query(TimeSlot, User).join(User, TimeSlot.claimed_by == User.id).filter(TimeSlot.status == 'refund_requested').all()
    all_slots = TimeSlot.query.all()
    
    return render_template('admin.html', users=users, pending_slots=pending_slots, refund_requests=refund_requests, all_slots=all_slots)

@app.route('/admin/create-slot', methods=['POST'])
def create_slot():
    if 'user_id' not in session or session['role'] != 'admin': 
        abort(403)
    
    slot_type = request.form['slot_type']
    date = request.form['date']
    start_time = request.form['start_time']
    
    new_slot = TimeSlot(slot_type=slot_type, date=date, start_time=start_time)
    
    if slot_type == 'specific':
        hours = int(request.form.get('spec_hours') or 0)
        mins = int(request.form.get('spec_mins') or 0)
        new_slot.duration_minutes = (hours * 60) + mins
    else:
        new_slot.end_time = request.form['end_time']
        
    db.session.add(new_slot)
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/update-balance/<int:user_id>', methods=['POST'])
def update_balance(user_id):
    if 'user_id' not in session or session['role'] != 'admin': 
        abort(403)
        
    user = User.query.get_or_404(user_id)
    hours = int(request.form.get('hours') or 0)
    mins = int(request.form.get('minutes') or 0)
    new_total = (hours * 60) + mins
    
    diff = new_total - user.balance_minutes
    user.balance_minutes = new_total
    
    receipt = Receipt(user_id=user.id, description="Admin balance manual adjustment", minutes_changed=diff)
    db.session.add(receipt)
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/decide-slot/<int:slot_id>/<string:action>', methods=['POST'])
def decide_slot(slot_id, action):
    if 'user_id' not in session or session['role'] != 'admin': 
        abort(403)
        
    slot = TimeSlot.query.get_or_404(slot_id)
    user = User.query.get(slot.claimed_by)
    message_text = request.form.get('admin_message', '').strip()
    
    if action == 'approve':
        slot.status = 'approved'
        user.balance_minutes -= slot.requested_duration
        
        receipt = Receipt(user_id=user.id, description=f"Massage Booked: {slot.date} @ {slot.start_time}", minutes_changed=-slot.requested_duration)
        db.session.add(receipt)
        
        notif = Notification(user_id=user.id, message=f"Your booking for {slot.date} has been APPROVED!")
        db.session.add(notif)
        
    elif action == 'deny':
        # Return slot to available status and remove the claim
        slot.status = 'available'
        slot.claimed_by = None
        slot.requested_duration = None
        
        reason = f" Reason: {message_text}" if message_text else ""
        notif = Notification(user_id=user.id, message=f"Your booking request for {slot.date} was declined.{reason}")
        db.session.add(notif)
        
    db.session.commit()
    return redirect('/secret-portal-0831')

@app.route('/admin/decide-refund/<int:slot_id>/<string:action>', methods=['POST'])
def decide_refund(slot_id, action):
    if 'user_id' not in session or session['role'] != 'admin': 
        abort(403)
        
    slot = TimeSlot.query.get_or_404(slot_id)
    user = User.query.get(slot.claimed_by)
    message_text = request.form.get('admin_message', '').strip()
    
    if action == 'approve':
        # Give back the minutes
        user.balance_minutes += slot.requested_duration
        
        receipt = Receipt(user_id=user.id, description=f"Cancelled/Refunded: {slot.date} @ {slot.start_time}", minutes_changed=slot.requested_duration)
        db.session.add(receipt)
        
        notif = Notification(user_id=user.id, message=f"Your cancellation request for {slot.date} was APPROVED. Credits returned.")
        db.session.add(notif)
        
        db.session.delete(slot) # Remove the slot entirely from the system
        
    elif action == 'deny':
        slot.status = 'approved' # Reset back to standard booked state
        reason = f" Reason: {message_text}" if message_text else ""
        notif = Notification(user_id=user.id, message=f"Your cancellation request for {slot.date} was declined.{reason}")
        db.session.add(notif)
        
    db.session.commit()
    return redirect('/secret-portal-0831')


# --- INITIALIZATION CLI COMMAND ---
# Run this inside your Render console shell (or locally) to build the profiles
@app.cli.command("init-db")
def init_db():
    db.create_all()
    # Create Mom (Gretta)
    if not User.query.filter_by(username='gretta').first():
        gretta = User(username='gretta', password='password123', name='Gretta (Mom)', balance_minutes=810) # 13.5 hours
        db.session.add(gretta)
    # Create Dad (Peter)
    if not User.query.filter_by(username='peter').first():
        peter = User(username='peter', password='password456', name='Peter (Dad)', balance_minutes=810)  # 13.5 hours
        db.session.add(peter)
    db.session.commit()
    print("Database built! Gretta and Peter profiles are online.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
