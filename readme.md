# Dynamic QR Attendance

Faculty shows a QR that changes every 0.5 s; students scan it from their phones. Works as a hosted web app
(Flask + Postgres) and as a faculty desktop app (`desktop.py`, local mode).

## Deploy to Render (free tier) - for a live demo
1. Push this folder to a GitHub repo (it must contain `render.yaml` at the root).
2. Render dashboard -> **New -> Blueprint** -> pick the repo. It creates the web service and a Postgres database
   in Singapore (both on the free plan; check Render's current free-tier limits, free databases can expire).
3. When asked, fill the environment values:
   - `FACULTY_USER` / `FACULTY_PASS` / `FACULTY_NAME` - the first faculty login (free tier has no shell, so it is created at startup)
   - `FACULTY_INVITE` - optional secret code so other faculty can self-register at /register
   (`SECRET_KEY` and `DATABASE_URL` are filled automatically.)
4. Open `https://<your-service>.onrender.com`. HTTPS is automatic, so phone cameras work with no warnings.

**Before a demo:** free services sleep after ~15 min idle and take up to a minute to wake. Open the site once
a few minutes beforehand. Faculty: log in -> add subject -> upload roster (.xlsx with a `roll_no` column) -> Start attendance
and show the QR full-screen. Students: /register, log in, Scan.

## Settings (environment variables)
| Name | Default | Meaning |
|---|---|---|
| `GRACE_SLOTS` | 4 | Accept codes up to N x 0.5 s old (raise for slow networks) |
| `CHECK_IP` | 0 | 1 = students must share the faculty's public IP (same campus Wi-Fi) |
| `FACULTY_INVITE` | unset | Enables faculty self-registration with this code |
| `DATABASE_URL` | unset | Postgres URL; if unset, local SQLite (`DB_PATH`) is used |

## Run locally
    pip install -r requirements.txt
    FACULTY_USER=prof FACULTY_PASS=changeme python app.py          # web server on :5000
    pip install -r requirements-desktop.txt
    FACULTY_USER=prof FACULTY_PASS=changeme python desktop.py      # faculty desktop app (LAN mode, own server)
