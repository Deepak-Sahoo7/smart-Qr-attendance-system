"""Dynamic-QR attendance: Flask + SQLite. Run: python app.py  (demo data seeded on first run)"""
import hmac, hashlib, io, os, secrets, sqlite3, time
from collections import defaultdict, deque
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps
import pandas as pd
from flask import (Flask, g, redirect, render_template, request, send_file,
                   session, jsonify, url_for, flash, abort)
from werkzeug.security import generate_password_hash, check_password_hash

SLOT_MS = 500          # QR changes every 0.5 s
GRACE_SLOTS = int(os.environ.get("GRACE_SLOTS", 4))   # codes up to ~2 s old are accepted (phone + network latency)
CHECK_IP = os.environ.get("CHECK_IP", "0") == "1"   # require student on teacher's network
DB = os.environ.get("DB_PATH", "attendance.db")

app = Flask(__name__)
ON_RENDER = bool(os.environ.get("RENDER"))
if ON_RENDER and not os.environ.get("SECRET_KEY"): raise SystemExit("SECRET_KEY must be set in production")
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)     # real client IP / https behind the host's proxy
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=ON_RENDER)
INVITE = os.environ.get("FACULTY_INVITE", "")                 # secret code that lets a faculty member self-register
HITS = defaultdict(deque)
def limited(key, n, per):
    now = time.time(); d = HITS[key]
    while d and now - d[0] > per: d.popleft()
    if len(d) >= n: return True
    d.append(now); return False

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw TEXT,
  name TEXT, role TEXT, roll_no TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS subjects(id INTEGER PRIMARY KEY, name TEXT, teacher_id INTEGER);
CREATE TABLE IF NOT EXISTS enrollments(subject_id INTEGER, student_id INTEGER, UNIQUE(subject_id, student_id));
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, subject_id INTEGER, secret TEXT,
  started_at REAL, ended_at REAL, teacher_ip TEXT);
CREATE TABLE IF NOT EXISTS attendance(session_id INTEGER, student_id INTEGER, marked_at REAL,
  UNIQUE(session_id, student_id));
"""

PG = bool(os.environ.get("DATABASE_URL"))
if PG:
    import psycopg2, psycopg2.extras
    DBERR = (sqlite3.IntegrityError, psycopg2.IntegrityError)
else:
    DBERR = (sqlite3.IntegrityError,)

def connect():
    if PG: return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row; return c

def fix(sql):                      # one SQL dialect for both SQLite (local) and Postgres (hosted)
    if not PG: return sql
    sql = sql.replace("?", "%s")
    if sql.lstrip().upper().startswith("INSERT OR IGNORE"):
        sql = sql.replace("INSERT OR IGNORE", "INSERT", 1) + " ON CONFLICT DO NOTHING"
    return sql

def exe(c, sql, a=(), ret=False):
    sql = fix(sql)
    if PG:
        cur = c.cursor(); cur.execute(sql + (" RETURNING id" if ret else ""), a); return cur
    return c.execute(sql, a)

def db():
    if "db" not in g: g.db = connect()
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, *a): return exe(db(), sql, a).fetchall()
def q1(sql, *a): return exe(db(), sql, a).fetchone()
class R:
    def __init__(s, cur, ret): s.rowcount = cur.rowcount; s.lastrowid = (cur.fetchone()["id"] if PG else cur.lastrowid) if ret else None
def run(sql, *a, ret=False):
    r = R(exe(db(), sql, a, ret), ret); db().commit(); return r

@app.get("/healthz")
def healthz(): return "ok"

def login_required(role):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if session.get("role") != role: return redirect(url_for("login"))
            return f(*a, **k)
        return w
    return deco

# ---------- QR token logic ----------
def now_slot(): return int(time.time() * 1000) // SLOT_MS
def sign(secret, sid, slot):
    return hmac.new(secret.encode(), f"{sid}.{slot}".encode(), hashlib.sha256).hexdigest()[:12]

# ---------- auth ----------
@app.route("/")
def index():
    return redirect(url_for("teacher_home" if session.get("role") == "teacher" else
                            "student_home" if session.get("role") == "student" else "login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if limited(("login", request.remote_addr), 10, 60):
            flash("Too many attempts. Wait a minute."); return render_template("login.html")
        u = q1("SELECT * FROM users WHERE username=?", request.form["username"].strip())
        if u and check_password_hash(u["pw"], request.form["password"]):
            session.clear(); session.update(uid=u["id"], role=u["role"], name=u["name"])
            return redirect(url_for("index"))
        flash("Wrong username or password.")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        teacher = bool(INVITE) and hmac.compare_digest(f.get("invite", "").strip(), INVITE)
        roll = f.get("roll_no", "").strip() or None
        if not teacher and not roll:
            flash("Roll number is required for students."); return render_template("register.html", invite=bool(INVITE))
        try:
            run("INSERT INTO users(username,pw,name,role,roll_no) VALUES(?,?,?,?,?)",
                f["username"].strip(), generate_password_hash(f["password"]), f["name"].strip(),
                "teacher" if teacher else "student", None if teacher else roll)
            flash("Account created. Log in to continue."); return redirect(url_for("login"))
        except DBERR:
            db().rollback(); flash("That username or roll number is already registered.")
    return render_template("register.html", invite=bool(INVITE))

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ---------- teacher ----------
@app.route("/teacher")
@login_required("teacher")
def teacher_home():
    subs = q("""SELECT s.*, (SELECT COUNT(*) FROM enrollments e WHERE e.subject_id=s.id) AS n_students,
      (SELECT COUNT(*) FROM sessions x WHERE x.subject_id=s.id) AS n_sessions,
      (SELECT id FROM sessions x WHERE x.subject_id=s.id AND x.ended_at IS NULL) AS live_id
      FROM subjects s WHERE teacher_id=?""", session["uid"])
    return render_template("teacher.html", subjects=subs)

@app.post("/teacher/subject")
@login_required("teacher")
def add_subject():
    run("INSERT INTO subjects(name,teacher_id) VALUES(?,?)", request.form["name"].strip(), session["uid"])
    return redirect(url_for("teacher_home"))

def own_subject(sid):
    s = q1("SELECT * FROM subjects WHERE id=? AND teacher_id=?", sid, session["uid"])
    return s or abort(404)

@app.post("/teacher/subject/<int:sid>/roster")
@login_required("teacher")
def upload_roster(sid):
    own_subject(sid)
    try:
        df = pd.read_excel(request.files["file"]); df.columns = [str(c).strip().lower() for c in df.columns]
        col = next(c for c in df.columns if c in ("roll_no", "roll no", "roll", "roll number"))
    except Exception:
        flash("Upload an .xlsx file with a 'roll_no' column."); return redirect(url_for("teacher_home"))
    ok = miss = 0
    for r in df[col].astype(str).str.strip():
        u = q1("SELECT id FROM users WHERE roll_no=? AND role='student'", r)
        if u: run("INSERT OR IGNORE INTO enrollments VALUES(?,?)", sid, u["id"]); ok += 1
        else: miss += 1
    flash(f"Enrolled {ok} students. {miss} roll numbers have not registered yet.")
    return redirect(url_for("teacher_home"))

@app.post("/teacher/subject/<int:sid>/start")
@login_required("teacher")
def start_session(sid):
    own_subject(sid)
    live = q1("SELECT id FROM sessions WHERE subject_id=? AND ended_at IS NULL", sid)
    if live: return redirect(url_for("live_session", xid=live["id"]))
    c = run("INSERT INTO sessions(subject_id,secret,started_at,teacher_ip) VALUES(?,?,?,?)",
            sid, secrets.token_hex(16), time.time(), request.remote_addr, ret=True)
    return redirect(url_for("live_session", xid=c.lastrowid))

def own_session(xid):
    s = q1("""SELECT x.*, s.name AS subject FROM sessions x JOIN subjects s ON s.id=x.subject_id
              WHERE x.id=? AND s.teacher_id=?""", xid, session["uid"])
    return s or abort(404)

@app.route("/teacher/session/<int:xid>")
@login_required("teacher")
def live_session(xid):
    return render_template("session.html", s=own_session(xid), slot_ms=SLOT_MS)

@app.get("/api/session/<int:xid>/tokens")
@login_required("teacher")
def tokens(xid):
    s = own_session(xid)
    if s["ended_at"]: return jsonify(ended=True)
    start = now_slot()
    return jsonify(server_ms=int(time.time() * 1000), slot_ms=SLOT_MS,
                   tokens={str(sl): sign(s["secret"], xid, sl) for sl in range(start, start + 40)})

@app.get("/api/session/<int:xid>/live")
@login_required("teacher")
def live(xid):
    own_session(xid)
    rows = q("""SELECT u.name, u.roll_no, a.marked_at FROM attendance a JOIN users u ON u.id=a.student_id
                WHERE a.session_id=? ORDER BY a.marked_at DESC""", xid)
    tot = q1("""SELECT COUNT(*) c FROM enrollments WHERE subject_id=(SELECT subject_id FROM sessions WHERE id=?)""", xid)["c"]
    return jsonify(total=tot, present=[dict(r) for r in rows])

@app.post("/teacher/session/<int:xid>/end")
@login_required("teacher")
def end_session(xid):
    own_session(xid); run("UPDATE sessions SET ended_at=? WHERE id=?", time.time(), xid)
    return redirect(url_for("teacher_home"))

def matrix(sid):
    """Rows = students, columns = sessions, plus totals. Shared by the Excel export."""
    sess = q("SELECT id, started_at FROM sessions WHERE subject_id=? ORDER BY started_at", sid)
    studs = q("""SELECT u.id, u.roll_no, u.name FROM enrollments e JOIN users u ON u.id=e.student_id
                 WHERE e.subject_id=? ORDER BY u.roll_no""", sid)
    marks = {(r["session_id"], r["student_id"]) for r in
             q("SELECT session_id, student_id FROM attendance WHERE session_id IN (SELECT id FROM sessions WHERE subject_id=?)", sid)}
    rows = []
    for st in studs:
        row = {"Roll No": st["roll_no"], "Name": st["name"]}; n = 0
        for x in sess:
            p = (x["id"], st["id"]) in marks; n += p
            row[time.strftime("%d-%b %H:%M", time.localtime(x["started_at"]))] = "P" if p else "A"
        row["Attended"] = n; row["Total"] = len(sess)
        row["Percent"] = round(100 * n / len(sess), 1) if sess else 0
        rows.append(row)
    return pd.DataFrame(rows)

@app.route("/teacher/subject/<int:sid>/export.xlsx")
@login_required("teacher")
def export(sid):
    s = own_subject(sid); buf = io.BytesIO()
    matrix(sid).to_excel(buf, index=False, sheet_name="Attendance"); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{s['name']}_attendance.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------- student ----------
def student_stats(uid):
    return q("""SELECT s.id, s.name,
      (SELECT COUNT(*) FROM sessions x WHERE x.subject_id=s.id) AS total,
      (SELECT COUNT(*) FROM attendance a JOIN sessions x ON x.id=a.session_id
         WHERE x.subject_id=s.id AND a.student_id=?) AS attended
      FROM subjects s JOIN enrollments e ON e.subject_id=s.id WHERE e.student_id=?""", uid, uid)

@app.route("/student")
@login_required("student")
def student_home():
    subs = [dict(r, pct=round(100 * r["attended"] / r["total"]) if r["total"] else 0) for r in student_stats(session["uid"])]
    live = q("""SELECT s.name FROM sessions x JOIN subjects s ON s.id=x.subject_id
                JOIN enrollments e ON e.subject_id=s.id WHERE x.ended_at IS NULL AND e.student_id=?""", session["uid"])
    return render_template("student.html", subjects=subs, live=live)

@app.route("/student/report/<int:sid>")
@login_required("student")
def student_report(sid):
    if not q1("SELECT 1 FROM enrollments WHERE subject_id=? AND student_id=?", sid, session["uid"]): abort(404)
    sub = q1("SELECT * FROM subjects WHERE id=?", sid)
    rows = q("""SELECT x.started_at, a.marked_at IS NOT NULL AS present FROM sessions x
                LEFT JOIN attendance a ON a.session_id=x.id AND a.student_id=? WHERE x.subject_id=?
                ORDER BY x.started_at DESC""", session["uid"], sid)
    hist = [dict(date=time.strftime("%a %d %b, %H:%M", time.localtime(r["started_at"])), present=bool(r["present"])) for r in rows]
    n = sum(h["present"] for h in hist)
    return render_template("report.html", sub=sub, hist=hist, n=n, total=len(hist),
                           pct=round(100 * n / len(hist)) if hist else 0)

@app.route("/student/scan")
@login_required("student")
def scan_page():
    return render_template("scan.html")

@app.post("/api/scan")
@login_required("student")
def scan():
    if limited(("scan", session["uid"]), 40, 10): return jsonify(ok=False, msg="Slow down."), 429
    try:
        tag, sid, slot, sig = (request.get_json()["payload"]).split(".")
        sid, slot = int(sid), int(slot)
    except Exception:
        return jsonify(ok=False, msg="That's not an attendance QR code."), 400
    s = q1("SELECT * FROM sessions WHERE id=?", sid)
    if tag != "SQA1" or not s or s["ended_at"]:
        return jsonify(ok=False, msg="This class session isn't open."), 400
    if not hmac.compare_digest(sig, sign(s["secret"], sid, slot)):
        return jsonify(ok=False, msg="Invalid QR code."), 400
    if abs(now_slot() - slot) > GRACE_SLOTS:
        return jsonify(ok=False, msg="Code expired. Scan the live screen, not a photo."), 400
    if not q1("SELECT 1 FROM enrollments WHERE subject_id=? AND student_id=?", s["subject_id"], session["uid"]):
        return jsonify(ok=False, msg="You're not enrolled in this subject."), 403
    if CHECK_IP and request.remote_addr != s["teacher_ip"]:
        return jsonify(ok=False, msg="Join the classroom network to mark attendance."), 403
    c = run("INSERT OR IGNORE INTO attendance VALUES(?,?,?)", sid, session["uid"], time.time())
    subj = q1("SELECT name FROM subjects WHERE id=?", s["subject_id"])["name"]
    return jsonify(ok=True, msg=f"Attendance marked for {subj}." if c.rowcount else f"Already marked for {subj}.")

def init():
    ddl = SCHEMA.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY").replace("REAL", "DOUBLE PRECISION") if PG else SCHEMA
    c = connect()
    for stmt in ddl.split(";"):
        if stmt.strip(): exe(c, stmt)
    u, p = os.environ.get("FACULTY_USER"), os.environ.get("FACULTY_PASS")   # first faculty account (no shell on free hosts)
    if u and p:
        exe(c, "INSERT OR IGNORE INTO users(username,pw,name,role) VALUES(?,?,?,'teacher')",
            (u, generate_password_hash(p), os.environ.get("FACULTY_NAME", "Faculty")))
    c.commit(); c.close()

init()   # runs once at import (gunicorn --preload)

if __name__ == "__main__":
    # Local use: phones need HTTPS for the camera -> HTTPS=1 (needs pyopenssl), or use a tunnel / the hosted site.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), ssl_context="adhoc" if os.environ.get("HTTPS") == "1" else None)
