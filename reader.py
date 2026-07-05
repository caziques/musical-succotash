import logging
import struct
import time
from typing import Any

from pymodbus.client import ModbusSerialClient

logger = logging.getLogger("reader")

BMS_PROTOCOLS = {
    0: "PYLON CAN", 1: "SACRED SUN RS485", 3: "DYNESS CAN",
    6: "GenixGreen RS485", 12: "PYLON RS485", 13: "VISION CAN",
    14: "WATTSONIC RS485", 15: "UNIPOWER RS485", 17: "LD RS485",
    19: "UNKNOWN RS485",
}

AUX_USAGE = {0: "Disabled", 1: "Smart Load", 2: "Generator"}

FAULT_CODES = {
    13: "Working mode change", 18: "AC over current", 20: "DC over current",
    23: "AC leak current", 24: "DC insulation impedance",
    26: "DC busbar imbalanced", 29: "Parallel comms cable",
    35: "No AC grid", 42: "AC line low voltage",
    47: "AC freq high/low", 56: "DC busbar voltage low",
    63: "ARC fault", 64: "Heat sink temp failure",
}


def _decode_faults(regs: tuple[int, ...]) -> str:
    faults = []
    off = 0
    for b16 in regs:
        for bit in range(16):
            if b16 & (1 << bit):
                n = bit + off + 1
                msg = FAULT_CODES.get(n, "")
                faults.append(f"F{n:02d} {msg}".strip())
        off += 16
    return ", ".join(faults) if faults else "None"


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
    "battery_voltage_3ph":{"reg": 587, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    # BMS-specific registers
    "bms_soc":           {"reg": 603, "count": 1, "unit": "%", "signed": False},
    "bms_voltage":       {"reg": 604, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "bms_current":       {"reg": 605, "count": 1, "scale": 0.01, "unit": "A", "signed": True},
    "battery_soc":       {"reg": 184, "count": 1, "unit": "%", "signed": False},
    # Fallback SOC registers for different firmware/models
    "battery_soc_std":   {"reg": 185, "count": 1, "unit": "%", "signed": False},
    "battery_soc_3ph":   {"reg": 588, "count": 1, "unit": "%", "signed": False},
    "battery_soc_bat1":  {"reg": 603, "count": 1, "unit": "%", "signed": False},
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

    # Faults
    "fault":               {"reg": 103, "count": 4, "unit": "", "signed": False, "is_fault": True},

    # Battery settings
    "battery_equalization_v":  {"reg": 201, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_absorption_v":    {"reg": 202, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_float_v":         {"reg": 203, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_max_charge_a":    {"reg": 210, "count": 1, "unit": "A", "signed": False},
    "battery_max_discharge_a": {"reg": 211, "count": 1, "unit": "A", "signed": False},
    "battery_shutdown_cap":    {"reg": 217, "count": 1, "unit": "%", "signed": False},
    "battery_restart_cap":     {"reg": 218, "count": 1, "unit": "%", "signed": False},
    "battery_low_cap":         {"reg": 219, "count": 1, "unit": "%", "signed": False},
    "battery_shutdown_v":      {"reg": 220, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_restart_v":       {"reg": 221, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_low_v":           {"reg": 222, "count": 1, "scale": 0.01, "unit": "V", "signed": False},
    "battery_charging_v":      {"reg": 312, "count": 1, "scale": 0.01, "unit": "V", "signed": False},

    # BMS
    "bms_protocol":        {"reg": 325, "count": 1, "unit": "", "signed": False, "is_bms_proto": True},

    # Settings / status
    "inverter_enabled":    {"reg": 43,  "count": 1, "unit": "", "signed": False, "is_bool": True},
    "grid_charge_enabled": {"reg": 232, "count": 1, "unit": "", "signed": False, "bitmask": 1},
    "aux_port_usage":      {"reg": 235, "count": 1, "unit": "", "signed": False},
    "priority_load":       {"reg": 243, "count": 1, "unit": "", "signed": False, "bitmask": 1},
    "solar_export":        {"reg": 247, "count": 1, "unit": "", "signed": False, "bitmask": 1},
    "use_timer":           {"reg": 248, "count": 1, "unit": "", "signed": False, "bitmask": 1},
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
    def __init__(self, port: str, slave_id: int = 1, baudrate: int = 9600, register_offset: int = 1):
        self.slave_id = slave_id
        self.register_offset = register_offset
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
            start = defn["reg"] + self.register_offset
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
                reg_start = defn["reg"] + self.register_offset
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

                # Temperature sensors have a -100 offset (reg value 1143 = 14.3°C)
                if defn.get("unit") == "°C":
                    value = round(value - 100, 1)

                # Bitmask sensors
                if "bitmask" in defn:
                    value = 1 if (value & defn["bitmask"]) else 0

                # Fault decoding
                if defn.get("is_fault"):
                    value = _decode_faults(regs)

                # BMS protocol
                if defn.get("is_bms_proto"):
                    value = BMS_PROTOCOLS.get(value, f"Unknown ({value})")

                result[name] = value
            except Exception as e:
                logger.warning("Parse error for %s: %s", name, e)

        if len(result) <= 2:
            result["read_ok"] = False

        # AUX port decoding
        aux = result.get("aux_port_usage")
        if aux is not None:
            result["aux_port_usage"] = AUX_USAGE.get(aux, f"Unknown ({aux})")

        return result
