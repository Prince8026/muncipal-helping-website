import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = "secretkey"

# This gives the database a perfect absolute path on Render's server
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'users.db')
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/')
def home():
    return redirect(url_for('login'))

db = SQLAlchemy(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)


class Inspection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    photo_path = db.Column(db.String(200))
    problem = db.Column(db.String(500))
    latitude = db.Column(db.String(50))
    longitude = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="Not Seen")
    resolved_date = db.Column(db.String(50), default="-")
    reply = db.Column(db.String(500), default="")
    user = db.relationship('User', backref=db.backref('inspections', lazy=True))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def is_admin():
    return current_user.is_authenticated and current_user.username == "prince"


def make_admin():
    """Create the fixed admin account or reset its password to the requested value."""
    admin = User.query.filter_by(username="prince").first()

    if admin is None:
        admin = User(
            username="prince",
            password=generate_password_hash("2610")
        )
        db.session.add(admin)
    else:
        if not check_password_hash(admin.password, "2610"):
            admin.password = generate_password_hash("2610")

    db.session.commit()


def format_date(date_text):
    """Convert HTML date value YYYY-MM-DD to DD-MM-YYYY."""
    if not date_text:
        return "-"
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return "-"


@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        # The fixed admin account cannot be created through public registration.
        if username.lower() == "prince":
            error = "The username 'prince' is reserved for the administrator."
        elif not username or not password:
            error = "Username and password are required."
        elif User.query.filter_by(username=username).first():
            error = "Username already exists. Please choose another username."
        else:
            hashed_password = generate_password_hash(password)
            new_user = User(username=username, password=hashed_password)
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('login'))

    return render_template('register.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))

        error = "Invalid username or password."

    return render_template('login.html', error=error)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    if is_admin():
        return redirect(url_for('admin_dashboard'))

    # Normal registered users can see ONLY their own submitted problems.
    inspections = Inspection.query.filter_by(user_id=current_user.id).order_by(
        Inspection.timestamp.desc()
    ).all()

    return render_template('dashboard.html', inspections=inspections)


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    # The administrator does not use the public upload page.
    if is_admin():
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        file = request.files.get('photo')
        lat = request.form.get('lat')
        lon = request.form.get('lon')
        problem = request.form.get('problem', '').strip()

        if file and file.filename and problem:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            new_inspection = Inspection(
                user_id=current_user.id,
                photo_path=filename,
                problem=problem,
                latitude=lat,
                longitude=lon,
                status="Not Seen",
                resolved_date="-",
                reply=""
            )

            db.session.add(new_inspection)
            db.session.commit()
            return redirect(url_for('dashboard'))

    return render_template('upload.html')


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/admin_dashboard', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    if not is_admin():
        return "Access denied", 403

    if request.method == 'POST':
        inspection_id = request.form.get('inspection_id')
        inspection = db.session.get(Inspection, int(inspection_id))

        if inspection is None:
            return "Problem not found", 404

        new_status = request.form.get('status', 'Not Seen')
        new_resolve_date = request.form.get('resolved_date', '')
        admin_reply = request.form.get('reply', '').strip()

        # Only the three requested statuses are accepted.
        if new_status not in ["Not Seen", "In Progress", "Completed"]:
            new_status = "Not Seen"

        # If an admin selects a resolve date, the problem becomes In Progress
        # unless the admin explicitly marks it Completed.
        if new_resolve_date:
            inspection.resolved_date = format_date(new_resolve_date)
            if new_status == "Not Seen":
                new_status = "In Progress"
        else:
            if new_status == "Not Seen":
                inspection.resolved_date = "-"
            else:
                # In Progress / Completed should have a resolve date.
                inspection.resolved_date = inspection.resolved_date if inspection.resolved_date != "-" else "-"

        inspection.status = new_status
        inspection.reply = admin_reply

        db.session.commit()
        return redirect(url_for('admin_dashboard'))

    status_filter = request.args.get('status', '').strip()

    if status_filter in ["Not Seen", "In Progress", "Completed"]:
        inspections = Inspection.query.filter_by(status=status_filter).order_by(
            Inspection.timestamp.desc()
        ).all()
    else:
        inspections = Inspection.query.order_by(Inspection.timestamp.desc()).all()

    return render_template(
        'admin_dashboard.html',
        inspections=inspections,
        selected_status=status_filter
    )

with app.app_context():
        db.create_all()
        make_admin()


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=8000)
