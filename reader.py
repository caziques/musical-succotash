import logging
import struct
import time
from typing import Any

from pymodbus.client import ModbusSerialClient

logger = logging.getLogger("reader")

SENSOR_DEFINITIONS = {
    "rated_power":       {"reg": 16, "count": 2, "scale": 0.1, "unit": "W", "signed": False},
    "date_time":         {"reg": 22, "count": 3, "unit": "", "signed": False, "is_datetime": True},
    "overall_state":     {"reg": 59, "count": 1, "unit": "", "signed": False},
    "day_active_energy": {"reg": 60, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_battery_charge":   {"reg": 70, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_battery_discharge":{"reg": 71, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_grid_import":   {"reg": 76, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "day_grid_export":   {"reg": 77, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "grid_frequency":    {"reg": 79, "count": 1, "scale": 0.01, "unit": "Hz", "signed": False},
    "day_load_energy":   {"reg": 84, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "dc_transformer_temp":{"reg": 90, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "radiator_temp":     {"reg": 91, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "environment_temp":  {"reg": 95, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "day_pv_energy":     {"reg": 108, "count": 1, "scale": 0.1, "unit": "kWh", "signed": False},
    "pv1_voltage":       {"reg": 109, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "pv1_current":       {"reg": 110, "count": 1, "scale": 0.1, "unit": "A", "signed": False},
    "pv2_voltage":       {"reg": 111, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "pv2_current":       {"reg": 112, "count": 1, "scale": 0.1, "unit": "A", "signed": False},
    "grid_voltage":      {"reg": 150, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "inverter_voltage":  {"reg": 154, "count": 1, "scale": 0.1, "unit": "V", "signed": False},
    "grid_current":      {"reg": 160, "count": 1, "scale": 0.01, "unit": "A", "signed": False},
    "gen_power":         {"reg": 166, "count": 1, "unit": "W", "signed": True},
    "grid_ld_power":     {"reg": 167, "count": 1, "unit": "W", "signed": True},
    "grid_power":        {"reg": 169, "count": 1, "unit": "W", "signed": True},
    "grid_ct_power":     {"reg": 172, "count": 1, "unit": "W", "signed": True},
    "inverter_power":    {"reg": 175, "count": 1, "unit": "W", "signed": True},
    "load_power":        {"reg": 178, "count": 1, "unit": "W", "signed": True},
    "battery_temp":      {"reg": 182, "count": 1, "scale": 0.1, "unit": "°C", "signed": False},
    "battery_voltage":   {"reg": 183, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_soc":       {"reg": 184, "count": 1, "unit": "%", "signed": False},
    "pv1_power":         {"reg": 186, "count": 1, "unit": "W", "signed": True},
    "pv2_power":         {"reg": 187, "count": 1, "unit": "W", "signed": True},
    "battery_power":     {"reg": 190, "count": 1, "unit": "W", "signed": True},
    "battery_current":   {"reg": 191, "count": 1, "scale": 0.01, "unit": "A", "signed": False},
    "inverter_frequency":{"reg": 193, "count": 1, "scale": 0.01, "unit": "Hz", "signed": False},
    "grid_connected":    {"reg": 194, "count": 1, "unit": "", "signed": False, "is_bool": True},
    "battery_charge_limit_current":  {"reg": 314, "count": 1, "unit": "A", "signed": True},
    "battery_discharge_limit_current":{"reg": 315, "count": 1, "unit": "A", "signed": True},
    "total_active_energy":       {"reg": 63,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_battery_charge":     {"reg": 72,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_battery_discharge":  {"reg": 74,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_grid_import":        {"reg": 78,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_grid_export":        {"reg": 81,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_load_energy":        {"reg": 85,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
    "total_pv_energy":          {"reg": 96,  "count": 2, "scale": 0.1, "unit": "kWh", "signed": False},
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

        for name, defn in SENSOR_DEFINITIONS.items():
            try:
                rr = self.client.read_holding_registers(
                    address=defn["reg"] - 1,
                    count=defn["count"],
                    slave=self.slave_id,
                )
                if rr.isError():
                    logger.warning("Read error for %s (reg %d)", name, defn["reg"])
                    continue

                regs = tuple(rr.registers)
                count = defn["count"]

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
                logger.warning("Exception reading %s: %s", name, e)

        if len(result) <= 2:
            result["read_ok"] = False

        return result
