#!/usr/bin/env python3
"""Sunsynk Inverter Data Collector.

Reads Modbus registers from a Sunsynk/Deye inverter via RS485 and stores
readings in a SQLite database. A web dashboard visualizes the data.

Usage:
    python main.py                           # Read inverter + serve dashboard
    python main.py --simulate                # Generate test data + serve dashboard
    python main.py --serve-only              # Just serve dashboard from existing data
    python main.py --read-only               # Just read and store data, no web server
"""

import argparse
import logging
import signal
import sys
import threading
import time

import urllib.request
import urllib.error
import json

import yaml

from database import Database
from reader import InverterReader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


class SimulatedReader:
    """Drop-in replacement for InverterReader that generates fake data."""
    import math
    import random

    def __init__(self):
        self._start = time.time()

    def connect(self):
        return True

    def close(self):
        pass

    def read_all(self):
        from reader import SENSOR_DEFINITIONS
        elapsed = time.time() - self._start
        hour_of_day = (elapsed / 3600) % 24
        is_daylight = 6 <= hour_of_day < 18
        sun_elev = max(0, self.math.sin(self.math.pi * (hour_of_day - 6) / 12)) if is_daylight else 0
        cf = self.random.uniform(0.1, 1.0)

        pv1 = int(0 if not is_daylight else 2800 * sun_elev * cf * self.random.uniform(0.9, 1.1))
        pv2 = int(0 if not is_daylight else 2200 * sun_elev * cf * self.random.uniform(0.85, 1.15))
        load = int(max(100, min(5000, self.random.gauss(800, 200))))
        soc = max(20, min(100, 50 + int(40 * self.math.sin(self.math.pi * (hour_of_day - 2) / 14))))
        batt = -int(min(pv1 + pv2, 2000)) if is_daylight and soc < 90 else int(min(load * 0.4, 1500)) if not is_daylight and soc > 30 else 0
        grid = load - pv1 - pv2 + batt + int(self.random.gauss(0, 30))

        return {
            "timestamp": time.time(),
            "read_ok": True,
            "grid_power": grid,
            "grid_voltage": round(self.random.gauss(230, 3), 1),
            "grid_current": round(abs(grid) / 230 + self.random.uniform(-0.5, 0.5), 2),
            "grid_frequency": round(self.random.gauss(50, 0.05), 2),
            "grid_connected": 1,
            "grid_ct_power": grid + int(self.random.gauss(0, 20)),
            "grid_ld_power": 0,
            "gen_power": 0,
            "load_power": load,
            "inverter_power": load + batt + int(self.random.gauss(0, 10)),
            "inverter_voltage": round(self.random.gauss(230, 2), 1),
            "inverter_frequency": round(self.random.gauss(50, 0.05), 2),
            "inverter_current": round((load + abs(batt)) / 230, 2),
            "pv1_power": pv1,
            "pv1_voltage": round(150 + 200 * sun_elev + self.random.uniform(-5, 5), 1),
            "pv1_current": round(pv1 / max(1, 200 * sun_elev) + self.random.uniform(-0.2, 0.2), 1),
            "pv2_power": pv2,
            "pv2_voltage": round(150 + 200 * sun_elev + self.random.uniform(-5, 5), 1),
            "pv2_current": round(pv2 / max(1, 200 * sun_elev) + self.random.uniform(-0.2, 0.2), 1),
            "battery_soc": soc,
            "battery_power": batt,
            "battery_voltage": round(48 + soc * 0.04 + self.random.uniform(-0.3, 0.3), 2),
            "battery_current": round(batt / max(1, 48 + soc * 0.04), 2),
            "battery_temp": round(25 + self.random.gauss(0, 2), 1),
            "battery_charge_limit_current": 50,
            "battery_discharge_limit_current": 50,
            "dc_transformer_temp": round(35 + self.random.gauss(0, 3), 1),
            "radiator_temp": round(32 + self.random.gauss(0, 2), 1),
            "environment_temp": round(20 + 5 * sun_elev + self.random.gauss(0, 1), 1),
            "rated_power": 8000.0,
            "overall_state": 1,
            "day_pv_energy": round(self.random.uniform(0, 15), 2),
            "day_load_energy": round(self.random.uniform(0, 12), 2),
            "day_grid_import": round(self.random.uniform(0, 5), 2),
            "day_grid_export": round(self.random.uniform(0, 8), 2),
            "day_battery_charge": round(self.random.uniform(0, 6), 2),
            "day_battery_discharge": round(self.random.uniform(0, 6), 2),
            "day_active_energy": round(self.random.uniform(0, 10), 2),
            "total_pv_energy": round(elapsed * 0.0005 + 1000, 1),
            "total_load_energy": round(elapsed * 0.0004 + 800, 1),
            "total_grid_import": round(elapsed * 0.0002 + 500, 1),
            "total_grid_export": round(elapsed * 0.00015 + 200, 1),
            "total_battery_charge": round(elapsed * 0.00025 + 300, 1),
            "total_battery_discharge": round(elapsed * 0.0002 + 250, 1),
            "total_active_energy": round(elapsed * 0.00045 + 1200, 1),
        }


def read_loop(reader, database: Database, interval: int, retention_days: int, stop_event: threading.Event):
    logger.info("Reader loop started (interval=%ds)", interval)
    last_purge = 0
    consecutive_failures = 0

    while not stop_event.is_set():
        try:
            data = reader.read_all()
            if data.get("read_ok"):
                database.insert(data)
                consecutive_failures = 0
                logger.debug(
                    "SOC=%s%% Grid=%sW Load=%sW PV=%sW Batt=%sW",
                    data.get("battery_soc", "?"),
                    data.get("grid_power", "?"),
                    data.get("load_power", "?"),
                    (data.get("pv1_power", 0) or 0) + (data.get("pv2_power", 0) or 0),
                    data.get("battery_power", "?"),
                )
            else:
                consecutive_failures += 1
                if consecutive_failures <= 3:
                    logger.warning("Read returned no valid data (failure %d)", consecutive_failures)
                else:
                    logger.error("Read returned no valid data (failure %d)", consecutive_failures)
        except Exception as e:
            consecutive_failures += 1
            logger.error("Read error: %s", e)

        # Reconnect after 10 consecutive failures
        if consecutive_failures >= 10:
            logger.warning("Too many failures, attempting reconnection...")
            try:
                reader.close()
                reader.connect()
                consecutive_failures = 0
                logger.info("Reconnected successfully")
            except Exception as re:
                logger.error("Reconnection failed: %s", re)

        if time.time() - last_purge > 86400:
            removed = database.purge_old(retention_days)
            if removed:
                logger.info("Purged %d old records", removed)
            last_purge = time.time()

        # WAL checkpoint every hour to prevent infinite growth
        if time.time() - getattr(read_loop, '_last_checkpoint', 0) > 3600:
            try:
                database.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                read_loop._last_checkpoint = time.time()
            except Exception:
                pass

        stop_event.wait(interval)

    logger.info("Reader loop stopped")


def evaluate_rules(database: Database, latest_data: dict) -> None:
    rules = database.get_rules_to_evaluate()
    now = time.time()
    for rule in rules:
        if not rule["enabled"]:
            continue
        if rule["last_fired"] and (now - rule["last_fired"]) < rule["cooldown_seconds"]:
            continue
        field = rule["trigger_field"]
        val = latest_data.get(field)
        if val is None:
            continue
        op = rule["trigger_op"]
        threshold = rule["trigger_value"]
        triggered = False
        if op == "lt":
            triggered = val < threshold
        elif op == "gt":
            triggered = val > threshold
        elif op == "eq":
            triggered = (val == threshold) or (bool(val) == bool(threshold))
        if not triggered:
            continue
        logger.info("Rule '%s' triggered (%s %s %s)", rule["name"], field, op, threshold)
        database.set_rule_last_fired(rule["id"], now)
        url = rule.get("action_url", "")
        if not url:
            continue
        try:
            body = rule.get("action_body", "{}")
            req = urllib.request.Request(
                url,
                data=body.encode() if body else None,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            logger.info("Webhook fired for rule '%s'", rule["name"])
        except Exception as e:
            logger.warning("Webhook failed for rule '%s': %s", rule["name"], e)


def rule_eval_loop(database: Database, interval: int, stop_event: threading.Event):
    logger.info("Rule evaluator started (interval=%ds)", interval)
    while not stop_event.is_set():
        try:
            latest = database.latest()
            if latest:
                evaluate_rules(database, latest)
        except Exception as e:
            logger.error("Rule eval error: %s", e)
        stop_event.wait(interval)


def main():
    parser = argparse.ArgumentParser(description="Sunsynk inverter data collector and dashboard")
    parser.add_argument("--config", "-c", default="config.yaml", help="Path to config YAML")
    parser.add_argument("--simulate", "-s", action="store_true", help="Use simulated data (no real inverter)")
    parser.add_argument("--serve-only", action="store_true", help="Only run dashboard, don't read inverter")
    parser.add_argument("--read-only", action="store_true", help="Only read and store data, no web server")
    parser.add_argument("--port", type=int, default=8080, help="Web dashboard port")
    parser.add_argument("--host", default="0.0.0.0", help="Web dashboard host")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    parser.add_argument("--retention", type=int, default=365, help="Data retention in days")
    parser.add_argument("--db", default="data/inverter.db", help="SQLite database path")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    db_path = args.db
    interval = args.interval
    retention = args.retention

    config = {}
    try:
        with open(args.config) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        pass

    db_path = config.get("database", {}).get("path", db_path)
    interval = config.get("polling", {}).get("interval_seconds", interval)
    retention = config.get("database", {}).get("retention_days", retention)

    database = Database(db_path)
    database.ensure_defaults()
    stop_event = threading.Event()

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if not args.serve_only:
        run_mode = database.get_setting("run_mode") or "simulate"
        if args.simulate or run_mode != "live":
            reader = SimulatedReader()
            logger.info("Using SIMULATED inverter data (run_mode=%s)", run_mode)
        else:
            port = database.get_setting("port") or config.get("inverter", {}).get("port", "/dev/ttyUSB0")
            slave_id = int(database.get_setting("slave_id") or config.get("inverter", {}).get("slave_id", 1))
            baudrate = int(database.get_setting("baudrate") or config.get("inverter", {}).get("baudrate", 9600))
            logger.info("Connecting to inverter on %s (id=%d, baud=%d)...", port, slave_id, baudrate)
            reader = InverterReader(port=port, slave_id=slave_id, baudrate=baudrate)
            if not reader.connect():
                logger.error("Failed to connect to inverter on %s. Falling back to simulate.", port)
                reader = SimulatedReader()
            else:
                logger.info("Connected to inverter in LIVE mode")

        reader_thread = threading.Thread(
            target=read_loop,
            args=(reader, database, interval, retention, stop_event),
            daemon=True,
        )
        reader_thread.start()

    # Start rule evaluator thread
    rule_thread = threading.Thread(
        target=rule_eval_loop,
        args=(database, interval, stop_event),
        daemon=True,
    )
    rule_thread.start()

    if not args.read_only:
        from dashboard import init_app, app
        init_app(database)
        logger.info("Dashboard starting on http://%s:%d", args.host, args.port)
        try:
            app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
        except KeyboardInterrupt:
            pass

    stop_event.set()
    if not args.serve_only and not args.simulate:
        reader.close()
    database.close()
    logger.info("Stopped")


if __name__ == "__main__":
    main()
