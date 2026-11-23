import os, re, sqlite3, unicodedata, threading
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # PostgreSQL driver is installed for hosted deployments
    psycopg = None
    dict_row = None

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("CLASSHUB_DB", os.path.join(BASE_DIR, "classhub.db"))
app = Flask(__name__)
app.secret_key = os.environ.get("CLASSHUB_SECRET") or "local-development-only-change-before-deploy"
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
if os.environ.get("RENDER") or os.environ.get("HTTPS_ONLY") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True

class PostgresConnection:
    """Small compatibility wrapper so the existing parameterized SQL works on SQLite and PostgreSQL."""
    def __init__(self, connection):
        self.connection = connection
    def execute(self, sql, params=()):
        return self.connection.execute(sql.replace("?", "%s"), params)
    def commit(self):
        self.connection.commit()
    def rollback(self):
        self.connection.rollback()
    def close(self):
        self.connection.close()

def connect_database():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("PostgreSQL driver missing. Install requirements.txt before starting CLASSHUB.")
        return PostgresConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def db():
    if "db" not in g:
        g.db = connect_database()
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

_init_lock = threading.Lock()
_db_initialized = False

def init_db():
    global _db_initialized
    with _init_lock:
        if _db_initialized:
            return
        conn = connect_database()
        try:
            if DATABASE_URL:
                conn.execute("""CREATE TABLE IF NOT EXISTS classes (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','student')),
                    class_id BIGINT REFERENCES classes(id),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS assignments (
                    id BIGSERIAL PRIMARY KEY,
                    class_id BIGINT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    due_date TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS topics (
                    id BIGSERIAL PRIMARY KEY,
                    class_id BIGINT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                    student_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Rejected')),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(class_id, normalized_title)
                )""")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_users_class_id ON users(class_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_topics_class_id ON topics(class_id)")
            else:
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS classes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','student')),
                    class_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(class_id) REFERENCES classes(id)
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    due_date TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Rejected')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE,
                    FOREIGN KEY(student_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(class_id, normalized_title)
                );
                CREATE INDEX IF NOT EXISTS idx_users_class_id ON users(class_id);
                CREATE INDEX IF NOT EXISTS idx_topics_class_id ON topics(class_id);
                """)
            conn.commit()
            _db_initialized = True
        finally:
            conn.close()

@app.before_request
def ensure_database_ready():
    init_db()

def norm_topic(value):
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db().execute("SELECT u.*, c.name AS class_name, c.code AS class_code FROM users u LEFT JOIN classes c ON c.id=u.class_id WHERE u.id=?", (uid,)).fetchone()

@app.context_processor
def inject_user():
    return {"me": current_user()}

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        if u["role"] != "admin":
            flash("Administrator access is required.", "danger")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapper

@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        class_code = request.form.get("class_code","").strip().upper()
        if len(name) < 2 or "@" not in email or len(password) < 8:
            flash("Enter your name, a valid email and a password of at least 8 characters.", "danger")
            return render_template("register.html")
        conn = db()
        class_row = conn.execute("SELECT * FROM classes WHERE UPPER(code)=?", (class_code,)).fetchone()
        if not class_row:
            flash("That class code was not found. Ask your lecturer for the correct code.", "danger")
            return render_template("register.html")
        try:
            conn.execute("INSERT INTO users(name,email,password_hash,role,class_id) VALUES(?,?,?,?,?)",
                         (name,email,generate_password_hash(password),"student",class_row["id"]))
            conn.commit()
            user = conn.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(?)",(email,)).fetchone()
            session.clear()
            session["user_id"] = user["id"]
            flash("Your student account is ready.", "success")
            return redirect(url_for("dashboard"))
        except (sqlite3.IntegrityError, psycopg.IntegrityError if psycopg else sqlite3.IntegrityError):
            conn.rollback()
            flash("An account with that email already exists.", "danger")
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = db().execute("SELECT * FROM users WHERE LOWER(email)=LOWER(?)",(email,)).fetchone()
        if user and check_password_hash(user["password_hash"],password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    conn = db()
    if u["role"] == "admin":
        classes = conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM users s WHERE s.class_id=c.id) student_count,
            (SELECT COUNT(*) FROM topics t WHERE t.class_id=c.id) topic_count FROM classes c ORDER BY c.name""").fetchall()
        pending = conn.execute("""SELECT t.*,u.name student_name,c.name class_name FROM topics t
            JOIN users u ON u.id=t.student_id JOIN classes c ON c.id=t.class_id
            WHERE t.status='Pending' ORDER BY t.created_at DESC LIMIT 8""").fetchall()
        return render_template("admin_dashboard.html", classes=classes, pending=pending)
    topics = conn.execute("SELECT * FROM topics WHERE class_id=? ORDER BY created_at DESC",(u["class_id"],)).fetchall()
    assignments = conn.execute("SELECT * FROM assignments WHERE class_id=? ORDER BY CASE WHEN due_date IS NULL OR due_date='' THEN 1 ELSE 0 END, due_date",(u["class_id"],)).fetchall()
    mine = conn.execute("SELECT * FROM topics WHERE student_id=?",(u["id"],)).fetchone()
    classmates = conn.execute("""SELECT t.*,u.name student_name FROM topics t JOIN users u ON u.id=t.student_id
        WHERE t.class_id=? ORDER BY t.created_at DESC""",(u["class_id"],)).fetchall()
    return render_template("student_dashboard.html", topics=classmates, assignments=assignments, mine=mine)

@app.route("/topics/register", methods=["POST"])
@login_required
def register_topic():
    u = current_user()
    if u["role"] != "student":
        return redirect(url_for("dashboard"))
    title = request.form.get("title","").strip()
    description = request.form.get("description","").strip()
    normalized = norm_topic(title)
    if len(title) < 4 or len(title) > 140:
        flash("Topic title must be between 4 and 140 characters.", "danger")
        return redirect(url_for("dashboard"))
    conn = db()
    existing = conn.execute("SELECT id FROM topics WHERE student_id=?",(u["id"],)).fetchone()
    if existing:
        flash("You have already registered a topic. Contact your lecturer if you need it changed.", "warning")
        return redirect(url_for("dashboard"))
    try:
        conn.execute("INSERT INTO topics(class_id,student_id,title,normalized_title,description) VALUES(?,?,?,?,?)",
                     (u["class_id"],u["id"],title,normalized,description))
        conn.commit()
        flash("Topic submitted. It is now visible to your class and awaiting lecturer review.", "success")
    except (sqlite3.IntegrityError, psycopg.IntegrityError if psycopg else sqlite3.IntegrityError):
        conn.rollback()
        flash("This topic is already registered by another student in your class. Please choose a different topic.", "danger")
    return redirect(url_for("dashboard"))

@app.route("/admin/classes", methods=["POST"])
@admin_required
def create_class():
    name = request.form.get("name","").strip()
    code = request.form.get("code","").strip().upper()
    if len(name)<2 or not re.fullmatch(r"[A-Z0-9_-]{3,20}",code):
        flash("Enter a class name and a code with 3–20 letters, numbers, hyphens or underscores.", "danger")
    else:
        try:
            db().execute("INSERT INTO classes(name,code) VALUES(?,?)",(name,code))
            db().commit()
            flash("Class created. Share its code with students.", "success")
        except (sqlite3.IntegrityError, psycopg.IntegrityError if psycopg else sqlite3.IntegrityError):
            db().rollback()
            flash("That class code is already in use.", "danger")
    return redirect(url_for("dashboard"))

@app.route("/admin/assignments", methods=["POST"])
@admin_required
def create_assignment():
    class_id = request.form.get("class_id","")
    title = request.form.get("title","").strip()
    desc = request.form.get("description","").strip()
    due = request.form.get("due_date","").strip()
    if not title or not class_id:
        flash("Choose a class and enter an assignment title.", "danger")
    else:
        try:
            db().execute("INSERT INTO assignments(class_id,title,description,due_date) VALUES(?,?,?,?)",(int(class_id),title,desc,due))
            db().commit()
            flash("Assignment published.", "success")
        except (ValueError, sqlite3.IntegrityError, psycopg.IntegrityError if psycopg else sqlite3.IntegrityError):
            db().rollback()
            flash("Please choose a valid class.", "danger")
    return redirect(url_for("dashboard"))

@app.route("/admin/topic/<int:topic_id>/<decision>", methods=["POST"])
@admin_required
def review_topic(topic_id, decision):
    if decision not in ("approve","reject"):
        return redirect(url_for("dashboard"))
    status = "Approved" if decision=="approve" else "Rejected"
    db().execute("UPDATE topics SET status=? WHERE id=?",(status,topic_id))
    db().commit()
    flash(f"Topic marked {status.lower()}.", "success")
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/admin/class/<int:class_id>")
@admin_required
def class_detail(class_id):
    conn = db()
    cls = conn.execute("SELECT * FROM classes WHERE id=?",(class_id,)).fetchone()
    if not cls:
        flash("Class not found.", "danger")
        return redirect(url_for("dashboard"))
    students = conn.execute("SELECT name,email,created_at FROM users WHERE class_id=? ORDER BY name",(class_id,)).fetchall()
    topics = conn.execute("SELECT t.*,u.name student_name FROM topics t JOIN users u ON u.id=t.student_id WHERE t.class_id=? ORDER BY t.created_at DESC",(class_id,)).fetchall()
    assignments = conn.execute("SELECT * FROM assignments WHERE class_id=? ORDER BY due_date",(class_id,)).fetchall()
    return render_template("class_detail.html", cls=cls, students=students, topics=topics, assignments=assignments)

@app.route("/setup", methods=["GET","POST"])
def setup():
    count = db().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if count:
        flash("Initial administrator setup has already been completed.", "warning")
        return redirect(url_for("login"))
    if request.method == "POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        if len(name)<2 or "@" not in email or len(password)<8:
            flash("Please provide a name, valid email and password of at least 8 characters.","danger")
        else:
            try:
                db().execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')",
                             (name,email,generate_password_hash(password)))
                db().commit()
                uid=db().execute("SELECT id FROM users WHERE LOWER(email)=LOWER(?)",(email,)).fetchone()["id"]
                session["user_id"]=uid
                flash("Administrator account created. Now create your first class.", "success")
                return redirect(url_for("dashboard"))
            except (sqlite3.IntegrityError, psycopg.IntegrityError if psycopg else sqlite3.IntegrityError):
                db().rollback()
                flash("An account with that email already exists.", "danger")
    return render_template("setup.html")

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"),404

@app.get("/health")
def health():
    db().execute("SELECT 1")
    return {"status":"ok", "database":"connected" if DATABASE_URL else "local-sqlite"}

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
