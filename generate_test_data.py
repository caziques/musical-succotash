"""Generate simulated Sunsynk inverter data for testing the dashboard."""
import time
import random
import math
from database import Database

def generate_hourly_pattern(hour_of_day: int, day_offset: int):
    """Return (is_daylight, sun_elevation, cloud_factor, load_factor) for a given hour."""
    is_daylight = 6 <= hour_of_day < 18
    sun_elevation = max(0, math.sin(math.pi * (hour_of_day - 6) / 12)) if is_daylight else 0

    # Yesterday (day_offset=1) is cloudier: lower PV, higher load
    # Today (day_offset=0) is sunny
    if day_offset == 0:
        cloud_factor = random.uniform(0.6, 1.0)
        load_base = 750
    else:
        cloud_factor = random.uniform(0.1, 0.55)
        load_base = 950
    return is_daylight, sun_elevation, cloud_factor, load_base


def generate_test_data(db_path: str = "data/inverter.db", hours: int = 50):
    db = Database(db_path)
    now = time.time()
    interval = 60

    total_points = hours * 60
    for i in range(total_points):
        ts = now - (total_points - i) * interval

        # Which day are we in? 0=today, 1=yesterday, etc.
        elapsed_hours = i / 60
        day_offset = int(elapsed_hours / 24)

        hour_of_day = ((i // 60) % 24)
        is_daylight, sun_elevation, cloud_factor, load_base = generate_hourly_pattern(hour_of_day, day_offset % 2)

        pv1 = int(0 if not is_daylight else 2800 * sun_elevation * cloud_factor * random.uniform(0.9, 1.1))
        pv2 = int(0 if not is_daylight else 2200 * sun_elevation * cloud_factor * random.uniform(0.85, 1.15))
        total_pv = pv1 + pv2

        load_power = int(random.gauss(load_base, 200))
        load_power = max(80, min(5500, load_power))

        soc_center = 50 + int(40 * math.sin(math.pi * (hour_of_day - 2) / 14))
        battery_soc = max(15, min(100, soc_center + int(random.gauss(0, 2))))

        if is_daylight and battery_soc < 90:
            battery_power = -int(min(total_pv * 0.3, 2000))
        elif not is_daylight and battery_soc > 30:
            battery_power = int(min(load_power * 0.4, 1500))
        else:
            battery_power = int(random.gauss(0, 50))

        grid_power = load_power - total_pv + battery_power
        grid_power = max(-5000, min(8000, grid_power + int(random.gauss(0, 30))))
        grid_connected = 1 if random.random() > 0.002 else 0  # rare outage

        tick = i * interval / 3600
        day_pv = tick * (total_pv / 1000) * 0.0001 + random.uniform(0, 0.01)
        day_load = tick * (load_power / 1000) * 0.0001 + random.uniform(0, 0.01)
        day_grid_import = max(0, grid_power) * tick / 1000 * 0.0001
        day_grid_export = max(0, -grid_power) * tick / 1000 * 0.0001
        day_batt_chg = max(0, -battery_power) * tick / 1000 * 0.0001
        day_batt_dchg = max(0, battery_power) * tick / 1000 * 0.0001

        data = {
            "timestamp": ts,
            "read_ok": True,
            "rated_power": 8000.0,
            "overall_state": 1,
            "grid_power": grid_power,
            "grid_voltage": round(random.gauss(230, 3), 1),
            "grid_current": round(abs(grid_power) / 230 + random.uniform(-0.5, 0.5), 2),
            "grid_frequency": round(random.gauss(50, 0.05), 2),
            "grid_connected": grid_connected,
            "grid_ct_power": grid_power + int(random.gauss(0, 20)),
            "grid_ld_power": 0,
            "gen_power": 0,
            "load_power": load_power,
            "inverter_power": load_power + battery_power + int(random.gauss(0, 10)),
            "inverter_voltage": round(random.gauss(230, 2), 1),
            "inverter_frequency": round(random.gauss(50, 0.05), 2),
            "inverter_current": round((load_power + abs(battery_power)) / 230, 2),
            "pv1_power": pv1,
            "pv1_voltage": round(150 + 200 * sun_elevation * cloud_factor + random.uniform(-5, 5), 1),
            "pv1_current": round(pv1 / max(1, 200 * sun_elevation * cloud_factor) + random.uniform(-0.2, 0.2), 1),
            "pv2_power": pv2,
            "pv2_voltage": round(150 + 200 * sun_elevation * cloud_factor + random.uniform(-5, 5), 1),
            "pv2_current": round(pv2 / max(1, 200 * sun_elevation * cloud_factor) + random.uniform(-0.2, 0.2), 1),
            "battery_soc": battery_soc,
            "battery_power": battery_power,
            "battery_voltage": round(48 + battery_soc * 0.04 + random.uniform(-0.3, 0.3), 2),
            "battery_current": round(battery_power / max(1, 48 + battery_soc * 0.04), 2),
            "battery_temp": round(25 + random.gauss(0, 2), 1),
            "battery_charge_limit_current": 50,
            "battery_discharge_limit_current": 50,
            "dc_transformer_temp": round(35 + random.gauss(0, 3), 1),
            "radiator_temp": round(32 + random.gauss(0, 2), 1),
            "environment_temp": round(20 + 5 * sun_elevation + random.gauss(0, 1), 1),
            "day_pv_energy": round(day_pv, 2),
            "day_load_energy": round(day_load, 2),
            "day_grid_import": round(day_grid_import, 2),
            "day_grid_export": round(day_grid_export, 2),
            "day_battery_charge": round(day_batt_chg, 2),
            "day_battery_discharge": round(day_batt_dchg, 2),
            "day_active_energy": round(day_load + random.uniform(0, 0.02), 2),
            "total_pv_energy": round(1000 + i * 0.01, 1),
            "total_load_energy": round(800 + i * 0.008, 1),
            "total_grid_import": round(500 + i * 0.004, 1),
            "total_grid_export": round(200 + i * 0.003, 1),
            "total_battery_charge": round(300 + i * 0.005, 1),
            "total_battery_discharge": round(250 + i * 0.004, 1),
            "total_active_energy": round(1200 + i * 0.009, 1),
        }
        db.insert(data)

        if i % 200 == 0:
            print(f"Generated {i}/{total_points} readings...")

    db.close()
    print(f"Done. Generated {total_points} readings over {hours} hours ({hours//24}d {hours%24}h).")


if __name__ == "__main__":
    generate_test_data()
