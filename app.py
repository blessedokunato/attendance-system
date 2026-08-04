import random
import os
import csv
from io import StringIO
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, Response, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta, timezone

from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-later-to-something-random')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///attendance.db')
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
mail = Mail(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[]
)
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

WAT = timezone(timedelta(hours=1))  # West Africa Time = UTC+1

def to_wat(dt):
    """Convert a UTC datetime to West Africa Time for display."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(WAT)

app.jinja_env.filters['to_wat'] = to_wat

# ---------- DATABASE MODELS ----------

class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Semester(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    matric_number = db.Column(db.String(50), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id'), nullable=False)

class AttendanceSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id'), nullable=False)
    topic = db.Column(db.String(150))
    start_time = db.Column(db.DateTime, default=datetime.utcnow)
    end_time = db.Column(db.DateTime, nullable=False)

class AttendanceRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('attendance_session.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    marked_at = db.Column(db.DateTime, default=datetime.utcnow)

# ---------- ROUTES ----------

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']

        existing_teacher = Teacher.query.filter_by(email=email).first()
        if existing_teacher:
            return "Email already registered. <a href='/signup'>Try again</a>"

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        new_teacher = Teacher(name=name, email=email, password=hashed_password)
        db.session.add(new_teacher)
        db.session.commit()

        flash('Account created! Please log in.')
        return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        teacher = Teacher.query.filter_by(email=email).first()

        if teacher and bcrypt.check_password_hash(teacher.password, password):
            session['teacher_id'] = teacher.id
            session['teacher_name'] = teacher.name
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password. Please try again.', 'error')
            return redirect(url_for('login'))
        
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    semesters = Semester.query.filter_by(teacher_id=session['teacher_id']).all()
    return render_template('dashboard.html', semesters=semesters, name=session['teacher_name'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        teacher = Teacher.query.filter_by(email=email).first()

        if teacher:
            token = serializer.dumps(email, salt='password-reset')
            reset_url = url_for('reset_password', token=token, _external=True)

            msg = Message('Reset Your Password - Attendance System',
                          sender=app.config['MAIL_USERNAME'],
                          recipients=[email])
            msg.body = f'''Hi {teacher.name},

You requested a password reset. Click the link below to set a new password:

{reset_url}

This link expires in 30 minutes. If you didn't request this, you can safely ignore this email.
'''
            mail.send(msg)

        # Always show the same message, whether or not the email exists —
        # this prevents someone from using this form to check which emails are registered
        flash('If that email is registered, a reset link has been sent.')
        return redirect(url_for('login'))

    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='password-reset', max_age=1800)  # 30 minutes
    except Exception:
        flash('This reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))

    teacher = Teacher.query.filter_by(email=email).first()
    if not teacher:
        flash('Account not found.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_password = request.form['password']
        teacher.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        db.session.commit()
        flash('Password updated! Please log in.')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

@app.route('/create_semester', methods=['GET', 'POST'])
def create_semester():
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form['name']
        new_semester = Semester(name=name, teacher_id=session['teacher_id'])
        db.session.add(new_semester)
        db.session.commit()
        return redirect(url_for('dashboard'))

    return render_template('create_semester.html')

@app.route('/semester/<int:semester_id>/add_student', methods=['GET', 'POST'])
def add_student(semester_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    semester = Semester.query.get_or_404(semester_id)

    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    if request.method == 'POST':
        name = request.form['name']
        matric_number = request.form['matric_number']
        new_student = Student(name=name, matric_number=matric_number, semester_id=semester.id)
        db.session.add(new_student)
        db.session.commit()
        flash(f'{name} added successfully.')
        return redirect(url_for('view_semester', semester_id=semester.id))

    return render_template('add_student.html', semester=semester)

@app.route('/student/<int:student_id>/edit', methods=['GET', 'POST'])
def edit_student(student_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    student = Student.query.get_or_404(student_id)
    semester = Semester.query.get_or_404(student.semester_id)

    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    if request.method == 'POST':
        student.name = request.form['name']
        student.matric_number = request.form['matric_number']
        db.session.commit()
        flash('Student details updated.')
        return redirect(url_for('view_semester', semester_id=semester.id))

    return render_template('edit_student.html', student=student, semester=semester)

@app.route('/student/<int:student_id>/delete', methods=['POST'])
def delete_student(student_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    student = Student.query.get_or_404(student_id)
    semester = Semester.query.get_or_404(student.semester_id)

    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    AttendanceRecord.query.filter_by(student_id=student.id).delete()
    db.session.delete(student)
    db.session.commit()

    flash('Student removed.')
    return redirect(url_for('view_semester', semester_id=semester.id))

@app.route('/semester/<int:semester_id>')
def view_semester(semester_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    semester = Semester.query.get_or_404(semester_id)

    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    students = Student.query.filter_by(semester_id=semester.id).all()
    sessions = AttendanceSession.query.filter_by(semester_id=semester.id).all()
    return render_template(
        'view_semester.html',
        semester=semester,
        students=students,
        sessions=sessions,
        now=datetime.utcnow()
    )

@app.route('/semester/<int:semester_id>/create_session', methods=['GET', 'POST'])
def create_session(semester_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    semester = Semester.query.get_or_404(semester_id)
    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    if request.method == 'POST':
        topic = request.form['topic']
        minutes = int(request.form['minutes'])

        new_session = AttendanceSession(
            semester_id=semester.id,
            topic=topic,
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(minutes=minutes)
        )
        db.session.add(new_session)
        db.session.commit()
        return redirect(url_for('session_created', session_id=new_session.id))

    return render_template('create_session.html', semester=semester)

@app.route('/session/<int:session_id>/created')
def session_created(session_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    attendance_session = AttendanceSession.query.get_or_404(session_id)
    return render_template('session_created.html', session=attendance_session)

@app.route('/session/<int:session_id>/end', methods=['POST'])
def end_session(session_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    attendance_session = AttendanceSession.query.get_or_404(session_id)
    semester = Semester.query.get_or_404(attendance_session.semester_id)

    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    attendance_session.end_time = datetime.utcnow()
    db.session.commit()

    flash('Session ended.')
    return redirect(url_for('session_results', session_id=attendance_session.id))


@app.route('/session/<int:session_id>/mark', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def mark_attendance(session_id):
    attendance_session = AttendanceSession.query.get_or_404(session_id)
    now = datetime.utcnow()

    is_open = attendance_session.start_time <= now <= attendance_session.end_time

    if request.method == 'POST':
        if not is_open:
            return render_template('attendance_result.html', success=False, message="Sorry, this attendance window has closed.")

        # Check the CAPTCHA answer
        expected_answer = session.get('captcha_answer')
        submitted_answer = request.form.get('captcha_answer', '').strip()

        if not expected_answer or submitted_answer != str(expected_answer):
            flash('Incorrect answer to the security question. Please try again.', 'error')
            return redirect(url_for('mark_attendance', session_id=session_id))

        matric_number = request.form['matric_number'].strip()

        student = Student.query.filter_by(
            semester_id=attendance_session.semester_id,
            matric_number=matric_number
        ).first()

        if not student:
            return render_template('attendance_result.html', success=False, message="Matric number not found for this semester.")

        existing = AttendanceRecord.query.filter_by(
            session_id=attendance_session.id,
            student_id=student.id
        ).first()

        if existing:
            return render_template('attendance_result.html', success=True, message=f"{student.name}, you've already been marked present for this session.")

        record = AttendanceRecord(session_id=attendance_session.id, student_id=student.id)
        db.session.add(record)
        db.session.commit()
        return render_template('attendance_result.html', success=True, message=f"Thanks {student.name}! You've been marked present.")

    # Generate a simple math question for GET requests (i.e. loading the page)
    import random
    num1 = random.randint(1, 10)
    num2 = random.randint(1, 10)
    session['captcha_answer'] = num1 + num2

    return render_template('mark_attendance.html', session=attendance_session, is_open=is_open, num1=num1, num2=num2)
@app.route('/session/<int:session_id>/results')
def session_results(session_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    attendance_session = AttendanceSession.query.get_or_404(session_id)
    semester = Semester.query.get_or_404(attendance_session.semester_id)

    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    all_students = Student.query.filter_by(semester_id=semester.id).all()
    records = AttendanceRecord.query.filter_by(session_id=attendance_session.id).all()
    present_student_ids = {record.student_id for record in records}

    return render_template(
        'session_results.html',
        session=attendance_session,
        semester=semester,
        all_students=all_students,
        present_student_ids=present_student_ids
    )

@app.route('/session/<int:session_id>/export')
def export_session_csv(session_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    attendance_session = AttendanceSession.query.get_or_404(session_id)
    semester = Semester.query.get_or_404(attendance_session.semester_id)

    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    all_students = Student.query.filter_by(semester_id=semester.id).all()
    records = AttendanceRecord.query.filter_by(session_id=attendance_session.id).all()
    present_student_ids = {record.student_id for record in records}

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Matric Number', 'Status'])

    for student in all_students:
        status = 'Present' if student.id in present_student_ids else 'Absent'
        writer.writerow([student.name, student.matric_number, status])

    csv_data = output.getvalue()
    output.close()

    filename = f"{attendance_session.topic}_attendance.csv".replace(' ', '_')

    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

@app.route('/semester/<int:semester_id>/summary')
def semester_summary(semester_id):
    if 'teacher_id' not in session:
        return redirect(url_for('login'))

    semester = Semester.query.get_or_404(semester_id)
    if semester.teacher_id != session['teacher_id']:
        return "Not authorized", 403

    students = Student.query.filter_by(semester_id=semester.id).all()
    sessions = AttendanceSession.query.filter_by(semester_id=semester.id).all()
    total_sessions = len(sessions)

    summary = []
    for student in students:
        attended = AttendanceRecord.query.filter_by(student_id=student.id) \
            .join(AttendanceSession) \
            .filter(AttendanceSession.semester_id == semester.id) \
            .count()

        percentage = round((attended / total_sessions) * 100, 1) if total_sessions > 0 else 0

        summary.append({
            'name': student.name,
            'matric_number': student.matric_number,
            'attended': attended,
            'total': total_sessions,
            'percentage': percentage
        })

    summary.sort(key=lambda s: s['percentage'])

    return render_template('semester_summary.html', semester=semester, summary=summary, total_sessions=total_sessions)

# ---------- RUN ----------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)