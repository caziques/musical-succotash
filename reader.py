import logging
import struct
import time
from typing import Any

from pymodbus.client import ModbusSerialClient

logger = logging.getLogger("reader")

SENSOR_DEFINITIONS = {
    "rated_power":       {"reg": 17, "count": 2, "scale": 0.1, "unit": "W", "signed": False},
    "date_time":         {"reg": 23, "count": 3, "unit": "", "signed": False, "is_datetime": True},
    "overall_state":     {"reg": 60, "count": 1, "unit": "", "signed": False},
    "day_active_energy": {"reg": 61, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_battery_charge":   {"reg": 71, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_battery_discharge":{"reg": 72, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_grid_import":   {"reg": 77, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_grid_export":   {"reg": 78, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "grid_frequency":    {"reg": 80, "count": 1, "scale": 0.01, "unit": "Hz", "signed": False},
    "day_load_energy":   {"reg": 85, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "dc_transformer_temp":{"reg": 91, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "radiator_temp":     {"reg": 92, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "environment_temp":  {"reg": 96, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "day_pv_energy":     {"reg": 109, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "pv1_voltage":       {"reg": 110, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "pv1_current":       {"reg": 111, "count": 1, "scale": 0.1, "unit": "A", "signed": False},
    "pv2_voltage":       {"reg": 112, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "pv2_current":       {"reg": 113, "count": 1, "scale": 0.1, "unit": "A", "signed": False},
    "grid_voltage":      {"reg": 151, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "inverter_voltage":  {"reg": 155, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "grid_current":      {"reg": 161, "count": 1, "scale": 0.01, "unit": "A", "signed": False},
    "gen_power":         {"reg": 167, "count": 1, "unit": "W", "signed": True},
    "grid_ld_power":     {"reg": 168, "count": 1, "unit": "W", "signed": True},
    "grid_power":        {"reg": 170, "count": 1, "unit": "W", "signed": True},
    "grid_ct_power":     {"reg": 173, "count": 1, "unit": "W", "signed": True},
    "inverter_power":    {"reg": 176, "count": 1, "unit": "W", "signed": True},
    "load_power":        {"reg": 179, "count": 1, "unit": "W", "signed": True},
    "battery_temp":      {"reg": 183, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "battery_voltage":   {"reg": 184, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_voltage_3ph":{"reg": 588, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    # BMS-specific registers
    "bms_soc":           {"reg": 604, "count": 1, "unit": "%", "signed": False},
    "bms_voltage":       {"reg": 605, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "bms_current":       {"reg": 606, "count": 1, "scale": 0.01, "unit": "A", "signed": True},
    "battery_soc":       {"reg": 185, "count": 1, "unit": "%", "signed": False},
    # Fallback SOC registers for different firmware/models
    "battery_soc_std":   {"reg": 186, "count": 1, "unit": "%", "signed": False},
    "battery_soc_3ph":   {"reg": 589, "count": 1, "unit": "%", "signed": False},
    "battery_soc_bat1":  {"reg": 604, "count": 1, "unit": "%", "signed": False},
    "pv1_power":         {"reg": 187, "count": 1, "unit": "W", "signed": True},
    "pv2_power":         {"reg": 188, "count": 1, "unit": "W", "signed": True},
    "battery_power":     {"reg": 191, "count": 1, "unit": "W", "signed": True},
    "battery_current":   {"reg": 192, "count": 1, "scale": 0.01, "unit": "A", "signed": False},
    "inverter_frequency":{"reg": 194, "count": 1, "scale": 0.01, "unit": "Hz", "signed": False},
    "grid_connected":    {"reg": 195, "count": 1, "unit": "", "signed": False, "is_bool": True},
    "battery_charge_limit_current":  {"reg": 315, "count": 1, "unit": "A", "signed": True},
    "battery_discharge_limit_current":{"reg": 316, "count": 1, "unit": "A", "signed": True},
    "total_active_energy":       {"reg": 64,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_battery_charge":     {"reg": 73,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_battery_discharge":  {"reg": 75,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_grid_import":        {"reg": 79,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_grid_export":        {"reg": 82,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_load_energy":        {"reg": 86,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_pv_energy":          {"reg": 97,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
}


def _to_unsigned(regs: tuple[int, ...]) -> int:
    if len(regs) == 1:
        return regs[0]
    return (regs[0] << 16) | regs[1]


def _to_signed(regs: tuple[int, ...], count: int) -> int:
    if count == 1:
        raw = regs[0]
        return raw if raw < 32768 else raw - 65536
    raw = (regs[0] << 16) | regs[1]
    return raw if raw < 2_147_483_648 else raw - 4_294_967_296


def _decode_datetime(regs: tuple[int, ...]) -> dict:
    return {
        "year": 2000 + (regs[0] >> 8),
        "month": regs[0] & 0xFF,
        "day": regs[1] >> 8,
        "hour": regs[1] & 0xFF,
        "minute": regs[2] >> 8,
        "second": regs[2] & 0xFF,
    }


class InverterReader:
    def __init__(self, port: str, slave_id: int = 1, baudrate: int = 9600):
        self.slave_id = slave_id
        self.client = ModbusSerialClient(
            port=port,
            baudrate=baudrate,
            parity="N",
            stopbits=1,
            bytesize=8,
            timeout=2,
        )

    def connect(self) -> bool:
        return self.client.connect()

    def close(self):
        self.client.close()

    def read_all(self) -> dict[str, Any]:
        result = {}
        result["timestamp"] = time.time()
        result["read_ok"] = True

        # Group sensors by contiguous register ranges for batched reads
        ranges = {}
        for name, defn in SENSOR_DEFINITIONS.items():
            start = defn["reg"]
            end = start + defn["count"] - 1
            rkey = (start, end)
            if rkey not in ranges:
                ranges[rkey] = []
            ranges[rkey].append((name, defn))

        # Merge overlapping/adjacent ranges (gap <= 2 registers)
        merged = []
        for (start, end) in sorted(ranges.keys()):
            if merged and start - merged[-1][1] <= 3:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        # Read each merged range in one call
        raw_data = {}
        for mstart, mend in merged:
            try:
                # Try pymodbus 3.x parameter names
                rr = None
                for param_name in ['slave', 'unit', 'device_id']:
                    try:
                        rr = self.client.read_holding_registers(
                            address=mstart - 1,
                            count=mend - mstart + 1,
                            **{param_name: self.slave_id},
                        )
                        break
                    except TypeError:
                        continue
                if rr is None:
                    logger.warning("Could not find valid parameter for read_holding_registers")
                    continue
                if not rr.isError():
                    for i, val in enumerate(rr.registers):
                        raw_data[mstart + i] = val
                else:
                    logger.warning("Batch read error for regs %d-%d", mstart, mend)
            except TypeError:
                # pymodbus >=3.6 uses 'unit' instead of 'slave'
                try:
                    rr = self.client.read_holding_registers(
                        address=mstart - 1,
                        count=mend - mstart + 1,
                        unit=self.slave_id,
                    )
                    if not rr.isError():
                        for i, val in enumerate(rr.registers):
                            raw_data[mstart + i] = val
                    else:
                        logger.warning("Batch read error for regs %d-%d", mstart, mend)
                except Exception as e2:
                    logger.warning("Batch read exception %d-%d: %s", mstart, mend, e2)

        # Parse individual sensors from batched data
        for name, defn in SENSOR_DEFINITIONS.items():
            try:
                reg_start = defn["reg"]
                count = defn["count"]
                regs = tuple(raw_data.get(reg_start + i, 0) for i in range(count))
                if all(v == 0 for v in regs) and not all(reg_start + i in raw_data for i in range(count)):
                    continue

                if defn.get("is_datetime"):
                    result[name] = _decode_datetime(regs)
                    continue

                if defn["signed"]:
                    value = _to_signed(regs, count)
                else:
                    value = _to_unsigned(regs)

                if defn.get("is_bool"):
                    value = 1 if value else 0

                if "scale" in defn:
                    value = round(value * defn["scale"], 2)

                result[name] = value
            except Exception as e:
                logger.warning("Parse error for %s: %s", name, e)

        if len(result) <= 2:
            result["read_ok"] = False

        return result
