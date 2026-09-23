"""RIGOL MHO5104 VISA/SCPI adapter based on the MHO/DHO5000 command family."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import ClassVar


def parse_tmc_block(raw: bytes) -> bytes:
    if len(raw) < 3 or raw[:1] != b"#" or not raw[1:2].isdigit():
        raise ValueError(f"invalid TMC block header: {raw[:16]!r}")
    digits = int(raw[1:2])
    header_end = 2 + digits
    if digits == 0 or len(raw) < header_end:
        raise ValueError("unsupported or incomplete TMC block header")
    size_field = raw[2:header_end]
    if not size_field.isdigit():
        raise ValueError(f"invalid TMC payload length: {size_field!r}")
    size = int(size_field)
    payload = raw[header_end : header_end + size]
    if len(payload) != size:
        raise ValueError(f"incomplete TMC payload: expected {size}, got {len(payload)}")
    return payload


class RigolMho5104Adapter:
    name = "rigol-mho5104"
    snapshot_schema = "rigol-mho5104-settings/v1"
    _measurements: ClassVar[dict[str, tuple[str, str, float]]] = {
        "frequency": ("FREQuency", "Hz", 1.0),
        "period": ("PERiod", "s", 1.0),
        "pduty": ("PDUTy", "%", 100.0),
        "vpp": ("VPP", "V", 1.0),
        "vmax": ("VMAX", "V", 1.0),
        "vmin": ("VMIN", "V", 1.0),
        "vavg": ("VAVG", "V", 1.0),
        "rise_time": ("RTIMe", "s", 1.0),
    }

    @staticmethod
    def parse_idn(idn: str) -> dict[str, str]:
        parts = [part.strip() for part in idn.split(",")]
        parts += [""] * (4 - len(parts))
        return dict(zip(("manufacturer", "model", "serial", "firmware"), parts[:4]))

    def matches(self, idn: str) -> bool:
        fields = self.parse_idn(idn)
        return (
            "RIGOL" in fields["manufacturer"].upper()
            and fields["model"].upper() == "MHO5104"
        )

    def verification(self, idn: str) -> str:
        fields = self.parse_idn(idn)
        if fields["model"].upper() == "MHO5104" and fields["firmware"] == "00.02.00":
            return "hardware_verified"
        return "adapter_matched_firmware_unverified"

    def next_error(self, scope) -> str:
        return scope.query(":SYSTem:ERRor:NEXT?").strip()

    @staticmethod
    def _safe_setting(value) -> str:
        text = str(value).strip()
        if not text or any(separator in text for separator in (";", "\r", "\n")):
            raise ValueError("snapshot setting contains an SCPI command separator")
        return text

    def measure(self, scope, channel: int, items: list[str]) -> list[dict]:
        if channel not in range(1, 5):
            raise ValueError("MHO5104 adapter accepts analog channels 1..4")
        results = []
        for item in items:
            key = item.lower()
            if key not in self._measurements:
                supported = ", ".join(sorted(self._measurements))
                raise ValueError(
                    f"unsupported measurement {item!r}; choose: {supported}"
                )
            scpi_item, unit, scale = self._measurements[key]
            raw = scope.query(f":MEASure:ITEM? {scpi_item},CHANnel{channel}").strip()
            results.append(
                {"item": key, "raw": raw, "value": float(raw) * scale, "unit": unit}
            )
        return results

    def screenshot(self, scope, output: Path) -> int:
        old_termination = scope.read_termination
        old_chunk_size = scope.chunk_size
        try:
            scope.read_termination = None
            scope.chunk_size = 1024 * 1024
            scope.write(":DISPlay:DATA? PNG")
            payload = parse_tmc_block(scope.read_raw())
            if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("instrument screenshot payload is not a PNG")
            output.write_bytes(payload)
            return len(payload)
        finally:
            scope.read_termination = old_termination
            scope.chunk_size = old_chunk_size

    def waveform(self, scope, channel: int, points: int, output: Path) -> dict:
        if channel not in range(1, 5):
            raise ValueError("MHO5104 adapter accepts analog channels 1..4")
        if not 1 <= points <= 1_000_000:
            raise ValueError("points must be between 1 and 1000000")
        before = {
            "source": scope.query(":WAVeform:SOURce?").strip(),
            "mode": scope.query(":WAVeform:MODE?").strip(),
            "format": scope.query(":WAVeform:FORMat?").strip(),
            "points": scope.query(":WAVeform:POINts?").strip(),
        }
        old_termination = scope.read_termination
        old_chunk_size = scope.chunk_size
        result = None
        operation_error = None
        try:
            scope.write(f":WAVeform:SOURce CHANnel{channel}")
            scope.write(":WAVeform:MODE NORMal")
            scope.write(":WAVeform:FORMat BYTE")
            scope.write(f":WAVeform:POINts {points}")
            preamble = [
                float(value) for value in scope.query(":WAVeform:PREamble?").split(",")
            ]
            if len(preamble) < 10:
                raise ValueError(f"invalid waveform preamble: {preamble!r}")
            scope.read_termination = None
            scope.chunk_size = 1024 * 1024
            scope.write(":WAVeform:DATA?")
            codes = parse_tmc_block(scope.read_raw())
            _, _, _, _, xinc, xorigin, xref, yinc, yorigin, yref = preamble[:10]
            with output.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("index", "time_s", "voltage_v", "code"))
                for index, code in enumerate(codes):
                    time_s = (index - xref) * xinc + xorigin
                    voltage_v = (code - yorigin - yref) * yinc
                    writer.writerow((index, time_s, voltage_v, code))
            result = {"points": len(codes), "preamble": preamble[:10]}
        except Exception as exc:  # noqa: BLE001 - preserve acquisition failure.
            operation_error = exc
        finally:
            scope.read_termination = old_termination
            scope.chunk_size = old_chunk_size
        restore_errors = []
        for command in (
            f":WAVeform:SOURce {before['source']}",
            f":WAVeform:MODE {before['mode']}",
            f":WAVeform:FORMat {before['format']}",
            f":WAVeform:POINts {before['points']}",
        ):
            try:
                scope.write(command)
            except Exception as exc:  # noqa: BLE001 - attempt every restore.
                restore_errors.append(f"{command}: {type(exc).__name__}: {exc}")
        if operation_error is not None:
            if restore_errors:
                details = "; ".join(restore_errors)
                raise RuntimeError(
                    f"waveform acquisition failed and settings restore was incomplete: {details}"
                ) from operation_error
            raise operation_error.with_traceback(operation_error.__traceback__)
        if restore_errors:
            raise RuntimeError(
                "waveform acquisition succeeded but settings restore was incomplete: "
                + "; ".join(restore_errors)
            )
        return result

    def snapshot(self, scope) -> dict[str, str]:
        settings = {}
        for channel in range(1, 5):
            for field, query in {
                "display": "DISPlay?",
                "probe": "PROBe?",
                "scale": "SCALe?",
                "offset": "OFFSet?",
                "coupling": "COUPling?",
            }.items():
                settings[f"channel{channel}_{field}"] = scope.query(
                    f":CHANnel{channel}:{query}"
                ).strip()
        for key, query in {
            "dvm_enable": ":DVM:ENABle?",
            "timebase_scale": ":TIMebase:MAIN:SCALe?",
            "timebase_offset": ":TIMebase:MAIN:OFFSet?",
            "trigger_source": ":TRIGger:EDGE:SOURce?",
            "trigger_slope": ":TRIGger:EDGE:SLOPe?",
            "trigger_level": ":TRIGger:EDGE:LEVel?",
        }.items():
            settings[key] = scope.query(query).strip()
        return settings

    def restore(self, scope, settings: dict[str, str]) -> None:
        ordered_commands = []
        for channel in range(1, 5):
            for field, command in {
                "display": "DISPlay",
                "probe": "PROBe",
                "scale": "SCALe",
                "offset": "OFFSet",
                "coupling": "COUPling",
            }.items():
                ordered_commands.append(
                    (f"channel{channel}_{field}", f":CHANnel{channel}:{command}")
                )
        mapping = {
            "dvm_enable": ":DVM:ENABle",
            "timebase_scale": ":TIMebase:MAIN:SCALe",
            "timebase_offset": ":TIMebase:MAIN:OFFSet",
            "trigger_source": ":TRIGger:EDGE:SOURce",
            "trigger_slope": ":TRIGger:EDGE:SLOPe",
            "trigger_level": ":TRIGger:EDGE:LEVel",
        }
        ordered_commands.extend(mapping.items())
        expected_keys = {key for key, _command in ordered_commands}
        actual_keys = set(settings)
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            unknown = sorted(actual_keys - expected_keys)
            raise ValueError(
                f"snapshot settings schema mismatch; missing={missing}, unknown={unknown}"
            )
        safe_settings = {
            key: self._safe_setting(settings[key]) for key, _command in ordered_commands
        }
        for key, command in ordered_commands:
            scope.write(f"{command} {safe_settings[key]}")
