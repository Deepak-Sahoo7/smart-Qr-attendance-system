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

## Security model (Phase 1 + 2)
- **Roster first:** faculty upload a roster (`roll_no`, optional `name`). New students get a one-time activation code, shown once
  on the upload result page. Students activate at `/activate` (roll number + code + new password); username = roll number.
  There is no open student sign-up.
- **Passkeys:** each student registers one passkey (fingerprint / face / screen lock) on their own phone. Every scan is two steps:
  `/api/scan/begin` (QR fresh, student enrolled -> one-time challenge) and `/api/scan/finish` (passkey signature over that challenge).
  A stolen password is useless without that phone. Only faculty can reset a passkey (Subjects -> Students & passkeys).
- **Integrity:** CSRF tokens on every POST, strict security headers, DB-backed rate limits, single-use challenges bound to the user,
  8-hour sessions, and a hash-chained audit log (`/teacher/audit.json` shows `chain_intact`).
- **Passkeys need a real domain over HTTPS** (e.g. your onrender.com address). They do not work on a LAN IP, so local desktop mode
  runs cookie-only (`REQUIRE_PASSKEY=0`). Set `RP_ID` to your hostname if Render's hostname is not detected.
- **Enrol passkeys in class** (first enrolment is the weakest moment: whoever holds the account then owns the passkey).
