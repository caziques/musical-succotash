import json
import logging
import os
import secrets
import time
from datetime import datetime
from functools import wraps

import urllib.request
import urllib.error
import urllib.parse

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import Database

logger = logging.getLogger("dashboard")
app = Flask(__name__)

db: Database | None = None


def init_app(database: Database):
    global db
    db = database


def generate_secret() -> str:
    secret_file = os.path.join(os.path.dirname(__file__), "data", ".secret")
    try:
        with open(secret_file) as f:
            return f.read().strip()
    except FileNotFoundError:
        key = secrets.token_hex(32)
        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        with open(secret_file, "w") as f:
            f.write(key)
        return key


app.secret_key = generate_secret()
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 30


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "unauthorized"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "admin required"}), 403
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user(username)
        if user and check_password_hash(user["password_hash"], password):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("index"))
        error = "Invalid username or password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Dashboard routes ─────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    tz = "Africa/Johannesburg"
    if db:
        tz = db.get_setting("timezone") or "Africa/Johannesburg"
    return render_template("index.html", role=session.get("role"), timezone=tz)


@app.route("/settings")
@admin_required
def settings_page():
    return render_template("settings.html")


@app.route("/automation")
@admin_required
def automation_page():
    return render_template("automation.html")


@app.route("/totals")
@login_required
def totals_page():
    return render_template("totals.html")


# ── API: data ────────────────────────────────────────────────────────────────

@app.route("/api/latest")
@api_login_required
def api_latest():
    record = db.latest()
    if record is None:
        return jsonify({"error": "no data"}), 404
    return jsonify(record)


@app.route("/api/history")
@api_login_required
def api_history():
    from_ts = request.args.get("from")
    to_ts = request.args.get("to")

    if from_ts and to_ts:
        try:
            start_ts = float(from_ts)
            end_ts = float(to_ts)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid from/to timestamps"}), 400
    else:
        try:
            hours = float(request.args.get("hours", "24"))
        except (TypeError, ValueError):
            hours = 24
        end_ts = time.time()
        start_ts = end_ts - hours * 3600

    records = db.query_range(start_ts, end_ts)

    # Down-sample: cap at ~500 points for chart performance
    if len(records) > 500:
        step = len(records) / 500
        sampled = []
        for i in range(500):
            sampled.append(records[int(i * step)])
        records = sampled

    return jsonify(records)


@app.route("/api/yesterday")
@api_login_required
def api_yesterday():
    """Return 24h of data from the same time range yesterday."""
    from_ts = request.args.get("from")
    to_ts = request.args.get("to")

    if from_ts and to_ts:
        try:
            end_float = float(to_ts)
            start_float = float(from_ts)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid timestamps"}), 400
    else:
        end_float = time.time()
        start_float = end_float - 86400

    span = end_float - start_float
    records = db.query_range(start_float - 86400, end_float - 86400)
    # Shift timestamps forward 24h so they align on x-axis
    for r in records:
        r["timestamp"] = r["timestamp"] + 86400
    return jsonify(records)


@app.route("/api/grid-status")
@api_login_required
def api_grid_status():
    """Return grid outage info: last connect time, current status."""
    record = db.latest()
    if not record:
        return jsonify({"connected": False, "outage_seconds": 0})
    connected = record.get("grid_connected", 1)
    if connected:
        return jsonify({"connected": True, "outage_seconds": 0})

    now = time.time()
    # Scan backwards to find when grid last disconnected
    rows = db.conn.execute(
        "SELECT timestamp, data FROM readings WHERE timestamp > ? ORDER BY timestamp DESC",
        (now - 86400 * 7,),
    ).fetchall()
    last_connected_ts = None
    for ts, data_json in rows:
        data = json.loads(data_json)
        if data.get("grid_connected", 1):
            last_connected_ts = ts
            break
    outage_secs = int(now - last_connected_ts) if last_connected_ts else 0
    return jsonify({"connected": False, "outage_seconds": outage_secs})


# ── API: current user ────────────────────────────────────────────────────────

@app.route("/api/me")
@api_login_required
def api_me():
    return jsonify({"username": session.get("user"), "role": session.get("role")})


# ── API: settings ────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
@admin_required
def api_get_settings():
    return jsonify(db.get_all_settings())


@app.route("/api/settings", methods=["POST"])
@admin_required
def api_save_settings():
    data = request.get_json(silent=True) or {}
    for key, value in data.items():
        db.set_setting(key, str(value))
    return jsonify({"ok": True})


# ── API: users ───────────────────────────────────────────────────────────────

@app.route("/api/users", methods=["GET"])
@admin_required
def api_list_users():
    return jsonify(db.list_users())


@app.route("/api/users", methods=["POST"])
@admin_required
def api_create_user():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "readonly")

    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    if role not in ("admin", "readonly"):
        return jsonify({"error": "role must be admin or readonly"}), 400

    ok = db.create_user(username, generate_password_hash(password), role)
    if not ok:
        return jsonify({"error": "username already exists"}), 409
    return jsonify({"ok": True})


@app.route("/api/users/<username>", methods=["DELETE"])
@admin_required
def api_delete_user(username: str):
    ok = db.delete_user(username)
    if not ok:
        return jsonify({"error": "cannot delete default admin"}), 403
    return jsonify({"ok": True})


@app.route("/api/users/<username>", methods=["PUT"])
@admin_required
def api_update_user(username: str):
    data = request.get_json(silent=True) or {}
    password_hash = generate_password_hash(data["password"]) if data.get("password") else None
    role = data.get("role")
    db.update_user(username, password_hash, role)
    return jsonify({"ok": True})


@app.route("/api/change-password", methods=["POST"])
@api_login_required
def api_change_password():
    data = request.get_json(silent=True) or {}
    current_pw = data.get("current_password", "")
    new_pw = data.get("new_password", "")

    if not new_pw or len(new_pw) < 4:
        return jsonify({"error": "new password must be at least 4 characters"}), 400

    username = session["user"]
    user = db.get_user(username)
    if not user or not check_password_hash(user["password_hash"], current_pw):
        return jsonify({"error": "current password is incorrect"}), 403

    db.update_user(username, generate_password_hash(new_pw), None)
    return jsonify({"ok": True})


# ── API: totals ──────────────────────────────────────────────────────────────

@app.route("/api/totals")
@api_login_required
def api_totals():
    days = request.args.get("days", "30")
    try:
        days = int(days)
    except ValueError:
        days = 30

    end_ts = time.time()
    start_ts = end_ts - days * 86400

    # Get the last reading of each day (day_*_energy is cumulative per day)
    rows = db.conn.execute(
        "SELECT timestamp, data FROM readings WHERE id IN ("
        "  SELECT MAX(id) FROM readings WHERE timestamp >= ? AND timestamp <= ? "
        "  GROUP BY CAST(timestamp / 86400 AS INTEGER)"
        ") ORDER BY timestamp DESC",
        (start_ts, end_ts),
    ).fetchall()

    result = []
    for ts, data_json in rows:
        record = json.loads(data_json)
        result.append({
            "date": time.strftime("%Y-%m-%d", time.localtime(ts)),
            "load": round(record.get("day_load_energy") or 0, 1),
            "solar": round(record.get("day_pv_energy") or 0, 1),
            "batt_charge": round(record.get("day_battery_charge") or 0, 1),
            "batt_discharge": round(record.get("day_battery_discharge") or 0, 1),
            "grid_import": round(record.get("day_grid_import") or 0, 1),
            "grid_export": round(record.get("day_grid_export") or 0, 1),
        })
    return jsonify(result)


# ── API: rules ───────────────────────────────────────────────────────────────

@app.route("/api/rules", methods=["GET"])
@admin_required
def api_list_rules():
    return jsonify(db.list_rules())


@app.route("/api/rules", methods=["POST"])
@admin_required
def api_create_rule():
    data = request.get_json(silent=True) or {}
    if not data.get("trigger_field") or not data.get("trigger_op"):
        return jsonify({"error": "trigger_field and trigger_op required"}), 400
    rid = db.create_rule(data)
    return jsonify({"ok": True, "id": rid})


@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
@admin_required
def api_update_rule(rule_id: int):
    data = request.get_json(silent=True) or {}
    ok = db.update_rule(rule_id, data)
    if not ok:
        return jsonify({"error": "no fields to update"}), 400
    return jsonify({"ok": True})


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
@admin_required
def api_delete_rule(rule_id: int):
    ok = db.delete_rule(rule_id)
    if not ok:
        return jsonify({"error": "rule not found"}), 404
    return jsonify({"ok": True})


# ── API: data management ─────────────────────────────────────────────────────

@app.route("/api/clear-data", methods=["POST"])
@admin_required
def api_clear_data():
    db.conn.execute("DELETE FROM readings")
    db.conn.execute("DELETE FROM rules")
    db.conn.execute("DELETE FROM settings")
    db.conn.commit()
    db.conn.execute("VACUUM")
    # Re-apply defaults (admin user preserved, re-create default settings)
    db.ensure_defaults()
    return jsonify({"ok": True})


@app.route("/api/generate-test-data", methods=["POST"])
@admin_required
def api_generate_test_data():
    data = request.get_json(silent=True) or {}
    hours = int(data.get("hours", 50))
    try:
        import random
        import math
        now = time.time()
        interval = 60
        total_points = hours * 60
        for i in range(total_points):
            ts = now - (total_points - i) * interval
            hour_of_day = ((i // 60) % 24)
            day_offset = int((i / 60) / 24)
            is_daylight = 6 <= hour_of_day < 18
            sun_elev = max(0, math.sin(math.pi * (hour_of_day - 6) / 12)) if is_daylight else 0
            cf = random.uniform(0.3, 0.7) if day_offset % 2 else random.uniform(0.6, 1.0)
            lb = 950 if day_offset % 2 else 750
            pv1 = int(0 if not is_daylight else 2800 * sun_elev * cf * random.uniform(0.9, 1.1))
            pv2 = int(0 if not is_daylight else 2200 * sun_elev * cf * random.uniform(0.85, 1.15))
            total_pv = pv1 + pv2
            load_power = max(80, min(5500, int(random.gauss(lb, 200))))
            soc = max(15, min(100, 50 + int(40 * math.sin(math.pi * (hour_of_day - 2) / 14)) + int(random.gauss(0, 2))))
            batt = -int(min(total_pv * 0.3, 2000)) if (is_daylight and soc < 90) else (int(min(load_power * 0.4, 1500)) if (not is_daylight and soc > 30) else int(random.gauss(0, 50)))
            grid = load_power - total_pv + batt + int(random.gauss(0, 30))
            record = {
                "timestamp": ts, "read_ok": True, "rated_power": 8000.0, "overall_state": 1,
                "grid_power": max(-5000, min(8000, grid)), "load_power": load_power,
                "pv1_power": pv1, "pv2_power": pv2, "battery_soc": soc, "battery_power": batt,
                "battery_voltage": round(48 + soc * 0.04 + random.uniform(-0.3, 0.3), 2),
                "battery_current": round(batt / max(1, 48 + soc * 0.04), 2),
                "battery_temp": round(25 + random.gauss(0, 2), 1),
                "battery_charge_limit_current": 50, "battery_discharge_limit_current": 50,
                "grid_voltage": round(random.gauss(230, 3), 1),
                "grid_frequency": round(random.gauss(50, 0.05), 2),
                "grid_current": round(abs(grid) / 230 + random.uniform(-0.5, 0.5), 2),
                "grid_connected": 1, "grid_ct_power": grid + int(random.gauss(0, 20)),
                "grid_ld_power": 0, "gen_power": 0,
                "inverter_power": load_power + batt + int(random.gauss(0, 10)),
                "inverter_voltage": round(random.gauss(230, 2), 1),
                "inverter_frequency": round(random.gauss(50, 0.05), 2),
                "inverter_current": round((load_power + abs(batt)) / 230, 2),
                "pv1_voltage": round(150 + 200 * sun_elev * cf + random.uniform(-5, 5), 1),
                "pv1_current": round(pv1 / max(1, 200 * sun_elev * cf) + random.uniform(-0.2, 0.2), 1),
                "pv2_voltage": round(150 + 200 * sun_elev * cf + random.uniform(-5, 5), 1),
                "pv2_current": round(pv2 / max(1, 200 * sun_elev * cf) + random.uniform(-0.2, 0.2), 1),
                "dc_transformer_temp": round(35 + random.gauss(0, 3), 1),
                "radiator_temp": round(32 + random.gauss(0, 2), 1),
                "environment_temp": round(20 + 5 * sun_elev + random.gauss(0, 1), 1),
            }
            db.insert(record)
        return jsonify({"ok": True, "hours": hours})
    except Exception as e:
        logger.exception("generate-test-data failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/go-live", methods=["POST"])
@admin_required
def api_go_live():
    import glob
    ports = []
    for p in ["/dev/ttyUSB*", "/dev/ttyAMA*", "/dev/ttyACM*", "/dev/serial/by-id/*"]:
        ports.extend(glob.glob(p))
    if not ports:
        return jsonify({"error": "No RS485 adapter detected."}), 404
    port = ports[0]
    db.set_setting("port", port)
    db.set_setting("run_mode", "live")
    logger.info("Go Live: %s, restarting...", port)
    from threading import Timer
    def _r():
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)
    Timer(0.5, _r).start()
    return jsonify({"ok": True, "port": port})


# ── API: updates ─────────────────────────────────────────────────────────────

@app.route("/api/check-update")
@admin_required
def api_check_update():
    import subprocess
    repo = db.get_setting("git_repo") or ""
    if not repo:
        return jsonify({"status": "no_repo", "message": "No git repository configured."})
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, cwd=os.path.dirname(__file__))
        local = r.stdout.strip()
        r2 = subprocess.run(["git", "ls-remote", repo, "HEAD"], capture_output=True, text=True, timeout=10)
        remote = r2.stdout.strip().split()[0] if r2.stdout.strip() else ""
        if local and remote and local != remote:
            return jsonify({"status": "update_available", "local": local[:8], "remote": remote[:8]})
        return jsonify({"status": "up_to_date", "local": local[:8] if local else "unknown"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/do-update", methods=["POST"])
@admin_required
def api_do_update():
    import subprocess
    repo = db.get_setting("git_repo") or ""
    if not repo:
        return jsonify({"error": "No git repository configured."}), 400
    base = os.path.dirname(__file__)
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=base)
        if repo not in r.stdout and repo not in r.stderr:
            subprocess.run(["git", "remote", "set-url", "origin", repo], capture_output=True, cwd=base)
    except Exception:
        pass
    try:
        subprocess.run(["git", "stash"], capture_output=True, cwd=base, timeout=10)
        r = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True, cwd=base, timeout=30)
        if r.returncode != 0:
            r2 = subprocess.run(["git", "pull", "origin", "master"], capture_output=True, text=True, cwd=base, timeout=30)
            if r2.returncode != 0:
                return jsonify({"error": f"git pull failed: {r.stderr or r2.stderr}"}), 500
        logger.info("Update pulled, restarting...")
        from threading import Timer
        def _restart():
            import sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
        Timer(0.5, _restart).start()
        return jsonify({"ok": True, "message": "Updated. Restarting..."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── API: discord ─────────────────────────────────────────────────────────────

@app.route("/api/discord-test", methods=["POST"])
@admin_required
def api_discord_test():
    url = db.get_setting("discord_url") or ""
    if not url:
        return jsonify({"error": "No Discord webhook URL configured"}), 400
    try:
        msg = {
            "embeds": [{
                "title": "Inverter Dashboard",
                "description": "Test notification - Discord integration is working!",
                "color": 3066993,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }]
        }
        req = urllib.request.Request(url, data=json.dumps(msg).encode(), headers={"Content-Type": "application/json", "User-Agent": "InverterDashboard/1.0"})
        urllib.request.urlopen(req, timeout=5)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.route("/api/weather")
@api_login_required
def api_weather():
    key = db.get_setting("weather_key") or ""
    city = db.get_setting("weather_city") or ""
    if not key or not city:
        return jsonify({"error": "Weather not configured. Set API key and city in Settings."}), 404

    cache_ts = db.get_setting("weather_cache_ts")
    if cache_ts:
        try:
            age = time.time() - float(cache_ts)
            if age < 600:  # cache for 10 minutes
                cached = db.get_setting("weather_cache_data")
                if cached:
                    return jsonify(json.loads(cached))
        except (ValueError, TypeError):
            pass

    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={urllib.parse.quote(city)}&appid={key}&units=metric"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"Weather API error: {e.code}"}), 502
    except Exception as e:
        return jsonify({"error": f"Weather fetch failed: {str(e)}"}), 502

    result = {
        "temp": round(data["main"]["temp"], 1),
        "feels_like": round(data["main"]["feels_like"], 1),
        "humidity": data["main"]["humidity"],
        "description": data["weather"][0]["description"].title(),
        "icon": data["weather"][0]["icon"],
        "wind_speed": round(data.get("wind", {}).get("speed", 0), 1),
        "clouds": data.get("clouds", {}).get("all", 0),
        "sunrise": data["sys"]["sunrise"],
        "sunset": data["sys"]["sunset"],
        "city": data["name"],
        "country": data["sys"].get("country", ""),
    }
    db.set_setting("weather_cache_ts", str(time.time()))
    db.set_setting("weather_cache_data", json.dumps(result))
    return jsonify(result)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/inverter.db")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    init_app(Database(args.db))
    app.run(host=args.host, port=args.port, debug=args.debug)
