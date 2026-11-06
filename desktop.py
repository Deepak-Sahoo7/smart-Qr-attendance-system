"""Faculty desktop app: live dynamic QR + built-in student server + attendance reports.
Run: python desktop.py   (students open the URL shown on screen)"""
import logging, secrets, socket, sqlite3, threading, time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import pandas as pd, qrcode
from werkzeug.security import check_password_hash
import app as A

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError: return "127.0.0.1"
    finally: s.close()

def start_server(port=5000):
    """Runs the student web app inside this process. HTTPS (self-signed) so phone cameras work."""
    try: import OpenSSL; ctx = "adhoc"
    except ImportError: ctx = None
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    threading.Thread(target=lambda: A.app.run(host="0.0.0.0", port=port, ssl_context=ctx,
                     threaded=True, use_reloader=False), daemon=True).start()
    return f"{'https' if ctx else 'http'}://{lan_ip()}:{port}"

def con():
    c = sqlite3.connect(A.DB, timeout=10); c.row_factory = sqlite3.Row; return c

def speak(text):   # optional name-calling (Windows SAPI, as in the original app)
    def go():
        try:
            import pythoncom, win32com.client
            pythoncom.CoInitialize(); win32com.client.Dispatch("SAPI.SpVoice").Speak(text)
        except Exception: pass
    threading.Thread(target=go, daemon=True).start()

class Desk(tk.Tk):
    QR = 440
    def __init__(s):
        super().__init__(); s.title("QR Attendance - Faculty"); s.geometry("1040x720"); s.minsize(900, 640)
        A.init(); con().execute("PRAGMA journal_mode=WAL").close()
        s.url = start_server(); s.uid = s.sid = s.secret = None; s.last = -1; s.seen = set(); s.polled = 0
        s.df = None; s.protocol("WM_DELETE_WINDOW", s.quit_app); s.login_ui()

    # ---------- login ----------
    def login_ui(s):
        f = s.lf = ttk.Frame(s, padding=40); f.place(relx=.5, rely=.5, anchor="center")
        ttk.Label(f, text="Faculty log in", font=("Segoe UI", 20, "bold")).grid(columnspan=2, pady=(0, 16))
        s.u, s.p = tk.StringVar(), tk.StringVar()
        for i, (t, v, sh) in enumerate([("Username", s.u, ""), ("Password", s.p, "*")], 1):
            ttk.Label(f, text=t).grid(row=i, column=0, sticky="e", padx=6, pady=4)
            e = ttk.Entry(f, textvariable=v, show=sh, width=26); e.grid(row=i, column=1, pady=4)
            e.bind("<Return>", lambda _: s.login())
        ttk.Button(f, text="Log in", command=s.login).grid(columnspan=2, pady=14)

    def login(s):
        c = con(); r = c.execute("SELECT * FROM users WHERE username=? AND role='teacher'", (s.u.get().strip(),)).fetchone(); c.close()
        if r and check_password_hash(r["pw"], s.p.get()):
            s.uid = r["id"]; s.lf.destroy(); s.main_ui()
        else: messagebox.showerror("Log in failed", "Wrong faculty username or password.")

    # ---------- main UI ----------
    def main_ui(s):
        nb = ttk.Notebook(s); nb.pack(fill="both", expand=True, padx=8, pady=8)
        a, r = ttk.Frame(nb, padding=12), ttk.Frame(nb, padding=12); nb.add(a, text="Take attendance"); nb.add(r, text="Reports")
        # attendance tab
        top = ttk.Frame(a); top.pack(fill="x")
        s.sub_a = ttk.Combobox(top, state="readonly", width=34); s.sub_a.pack(side="left")
        s.btn = ttk.Button(top, text="Start attendance", command=s.toggle); s.btn.pack(side="left", padx=8)
        s.sound = tk.BooleanVar(value=True); ttk.Checkbutton(top, text="Call names aloud", variable=s.sound).pack(side="left")
        ttk.Button(top, text="Add subject", command=s.add_subject).pack(side="right")
        ttk.Label(a, text=f"Students open  {s.url}  and sign in. Same Wi-Fi as this computer.", foreground="#0f766e").pack(anchor="w", pady=6)
        body = ttk.Frame(a); body.pack(fill="both", expand=True)
        s.qr = tk.Canvas(body, width=s.QR, height=s.QR, bg="white", highlightthickness=0); s.qr.pack(side="left", padx=(0, 16))
        s.idle()
        side = ttk.Frame(body); side.pack(side="left", fill="both", expand=True)
        s.cnt = ttk.Label(side, text="Present: 0 / 0", font=("Segoe UI", 15, "bold")); s.cnt.pack(anchor="w")
        s.tree = ttk.Treeview(side, columns=("roll", "name", "time"), show="headings")
        for c, t, w in (("roll", "Roll no", 80), ("name", "Name", 180), ("time", "Time", 80)): s.tree.heading(c, text=t); s.tree.column(c, width=w)
        s.tree.pack(fill="both", expand=True, pady=6)
        # report tab
        top = ttk.Frame(r); top.pack(fill="x")
        s.sub_r = ttk.Combobox(top, state="readonly", width=34); s.sub_r.pack(side="left"); s.sub_r.bind("<<ComboboxSelected>>", lambda _: s.load_report())
        ttk.Button(top, text="Refresh", command=s.load_report).pack(side="left", padx=8)
        ttk.Button(top, text="Export to Excel", command=s.export).pack(side="right")
        ttk.Button(top, text="Upload roster (.xlsx)", command=s.roster).pack(side="right", padx=8)
        s.rt = ttk.Treeview(r, show="headings"); s.rt.pack(fill="both", expand=True, pady=8)
        s.rt.tag_configure("low", foreground="#b4401a")
        s.refresh_subjects(); live = con().execute("SELECT x.id FROM sessions x JOIN subjects s ON s.id=x.subject_id WHERE x.ended_at IS NULL AND s.teacher_id=?", (s.uid,)).fetchone()
        if live: s.resume(live["id"])

    def refresh_subjects(s):
        c = con(); s.subs = c.execute("SELECT id,name FROM subjects WHERE teacher_id=?", (s.uid,)).fetchall(); c.close()
        names = [x["name"] for x in s.subs]
        for cb in (s.sub_a, s.sub_r):
            cb["values"] = names
            if names and not cb.get(): cb.current(0)

    def add_subject(s):
        n = simpledialog.askstring("New subject", "Subject name:", parent=s)
        if n and n.strip():
            c = con(); c.execute("INSERT INTO subjects(name,teacher_id) VALUES(?,?)", (n.strip(), s.uid)); c.commit(); c.close(); s.refresh_subjects()

    def subject(s, cb):
        i = cb.current(); return s.subs[i]["id"] if i >= 0 else None

    # ---------- live session ----------
    def idle(s, msg="Press Start attendance to show the live QR"):
        s.qr.delete("all"); s.qr.create_text(s.QR // 2, s.QR // 2, text=msg, width=s.QR - 60, justify="center", font=("Segoe UI", 14), fill="#5d6b6e")

    def toggle(s): s.end() if s.sid else s.start()

    def start(s):
        sub = s.subject(s.sub_a)
        if not sub: return messagebox.showinfo("No subject", "Add a subject first.")
        c = con(); live = c.execute("SELECT id FROM sessions WHERE subject_id=? AND ended_at IS NULL", (sub,)).fetchone()
        if live: sid = live["id"]
        else:
            sid = c.execute("INSERT INTO sessions(subject_id,secret,started_at,teacher_ip) VALUES(?,?,?,?)",
                            (sub, secrets.token_hex(16), time.time(), lan_ip())).lastrowid; c.commit()
        c.close(); s.resume(sid)

    def resume(s, sid):
        c = con(); r = c.execute("SELECT secret, subject_id FROM sessions WHERE id=?", (sid,)).fetchone(); c.close()
        s.sid, s.secret, s.last, s.seen, s.polled = sid, r["secret"], -1, set(), 0
        s.sub_a.current([x["id"] for x in s.subs].index(r["subject_id"])); s.sub_a["state"] = "disabled"
        s.btn["text"] = "End attendance"; s.tree.delete(*s.tree.get_children()); s.tick()

    def tick(s):
        if not s.sid: return
        slot = int(time.time() * 1000) // A.SLOT_MS
        if slot != s.last:
            s.last = slot; s.draw(f"SQA1.{s.sid}.{slot}.{A.sign(s.secret, s.sid, slot)}")
        if time.time() - s.polled > 1: s.polled = time.time(); s.poll()
        s.after(30, s.tick)

    def draw(s, text):
        q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4); q.add_data(text); q.make(fit=True)
        m = q.get_matrix(); n = len(m); px = s.QR // n; off = (s.QR - px * n) // 2
        s.qr.delete("all"); s.qr.create_rectangle(0, 0, s.QR, s.QR, fill="white", outline="")
        for y, row in enumerate(m):
            x = 0
            while x < n:
                if row[x]:
                    e = x
                    while e < n and row[e]: e += 1
                    s.qr.create_rectangle(off + x * px, off + y * px, off + e * px, off + (y + 1) * px, fill="black", outline=""); x = e
                else: x += 1

    def poll(s):
        c = con()
        rows = c.execute("SELECT u.roll_no,u.name,a.marked_at FROM attendance a JOIN users u ON u.id=a.student_id WHERE a.session_id=? ORDER BY a.marked_at", (s.sid,)).fetchall()
        tot = c.execute("SELECT COUNT(*) n FROM enrollments WHERE subject_id=(SELECT subject_id FROM sessions WHERE id=?)", (s.sid,)).fetchone()["n"]; c.close()
        for r in rows:
            if r["roll_no"] not in s.seen:
                s.seen.add(r["roll_no"]); s.tree.insert("", 0, values=(r["roll_no"], r["name"], time.strftime("%H:%M:%S", time.localtime(r["marked_at"]))))
                if s.sound.get(): speak(f"{r['name']} present")
        s.cnt["text"] = f"Present: {len(rows)} / {tot}"

    def end(s):
        if not messagebox.askyesno("End attendance", "Stop the QR and close this session?"): return
        c = con(); c.execute("UPDATE sessions SET ended_at=? WHERE id=?", (time.time(), s.sid)); c.commit(); c.close()
        s.sid = None; s.sub_a["state"] = "readonly"; s.btn["text"] = "Start attendance"; s.idle("Session ended. See the Reports tab."); s.load_report()

    # ---------- reports ----------
    def load_report(s):
        sid = s.subject(s.sub_r)
        if not sid: return
        with A.app.app_context(): s.df = A.matrix(sid)
        s.rt.delete(*s.rt.get_children()); cols = list(s.df.columns); s.rt["columns"] = cols
        for c in cols: s.rt.heading(c, text=c); s.rt.column(c, width=150 if c == "Name" else 85, anchor="w" if c == "Name" else "center")
        for _, r in s.df.iterrows(): s.rt.insert("", "end", values=list(r), tags=("low",) if r["Percent"] < 75 and r["Total"] else ())

    def export(s):
        s.load_report()
        if s.df is None or s.df.empty: return messagebox.showinfo("Nothing to export", "No enrolled students yet. Upload a roster first.")
        f = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], initialfile=f"{s.sub_r.get()}_attendance.xlsx")
        if f: s.df.to_excel(f, index=False, sheet_name="Attendance"); messagebox.showinfo("Saved", f)

    def roster(s):
        sid = s.subject(s.sub_r); f = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
        if not (sid and f): return
        try:
            df = pd.read_excel(f, dtype=str); df.columns = [str(x).strip().lower() for x in df.columns]
            col = next(x for x in df.columns if x in ("roll_no", "roll no", "roll", "roll number"))
        except Exception: return messagebox.showerror("Can't read file", "Use an .xlsx file with a 'roll_no' column.")
        with A.app.app_context(): r = A.import_roster(sid, df)
        s.load_report()
        if r["codes"]:
            f2 = filedialog.asksaveasfilename(title="Save activation codes for new students", defaultextension=".xlsx", initialfile="activation_codes.xlsx")
            if f2: pd.DataFrame(r["codes"], columns=["roll_no", "name", "activation_code"]).to_excel(f2, index=False)
        messagebox.showinfo("Roster uploaded", f"Enrolled {r['enrolled']} students. {len(r['codes'])} new activation codes created.")

    def quit_app(s):
        if s.sid and messagebox.askyesno("Attendance is live", "End the live session before closing?"):
            c = con(); c.execute("UPDATE sessions SET ended_at=? WHERE id=?", (time.time(), s.sid)); c.commit(); c.close()
        s.destroy()

if __name__ == "__main__":
    Desk().mainloop()
