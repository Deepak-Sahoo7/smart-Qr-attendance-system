# Dynamic QR Attendance (web)
pip install -r requirements.txt && python app.py      # http://localhost:5000
Demo teacher: teacher / teacher123. Students sign up at /register.
Phones need HTTPS for the camera: `HTTPS=1 python app.py` (self-signed) or tunnel with ngrok/cloudflared.
Env: SECRET_KEY, DB_PATH, CHECK_IP=1 (students must share the teacher's network).

## Faculty desktop app
python desktop.py - log in as a faculty user (demo: teacher / teacher123). It starts the student server
itself, shows the live 0.5 s QR, lists scans as they happen, calls names aloud (Windows), and handles
roster upload + Excel export. Leave CHECK_IP off in this mode: students are already on the faculty LAN.
