# /// script
# requires-python = ">=3.8"
# dependencies = ["pyvisa>=1.14,<2"]
# ///
"""Safe, structured VISA/SCPI entry point for supported oscilloscopes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from adapters import select_adapter


def emit(payload: dict, *, stream=sys.stdout) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream, flush=True)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def open_manager(backend: str | None):
    try:
        import pyvisa
    except ImportError as exc:
        raise RuntimeError(
            "pyvisa is unavailable; run this script with uv run"
        ) from exc
    return pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()


def connect(manager, resource: str, timeout_ms: int):
    scope = manager.open_resource(resource)
    scope.timeout = timeout_ms
    scope.read_termination = "\n"
    scope.write_termination = "\n"
    return scope


def identify(scope) -> tuple[str, object | None]:
    idn = scope.query("*IDN?").strip()
    return idn, select_adapter(idn)


def base_result(resource: str, idn: str, adapter) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resource": resource,
        "idn": idn,
        "adapter": adapter.name if adapter else None,
        "verification": adapter.verification(idn) if adapter else "unsupported",
    }


def require_adapter(idn: str, adapter):
    if adapter is None:
        raise RuntimeError(f"no verified adapter matches instrument: {idn}")
    verification = adapter.verification(idn)
    if verification != "hardware_verified":
        raise RuntimeError(
            "instrument is recognized but adapter execution is restricted to "
            f"hardware_verified devices; current verification: {verification}"
        )
    return adapter


def command_detect(args) -> dict:
    manager = open_manager(args.backend)
    instruments = []
    try:
        for resource in manager.list_resources(args.visa_pattern):
            item = {"resource": resource}
            scope = None
            try:
                scope = connect(manager, resource, args.timeout_ms)
                idn, adapter = identify(scope)
                item.update(base_result(resource, idn, adapter))
            except Exception as exc:  # noqa: BLE001 - preserve each VISA probe failure.
                item["error"] = str(exc)
            finally:
                if scope is not None:
                    scope.close()
            instruments.append(item)
        return {
            "visa_pattern": args.visa_pattern,
            "instruments": instruments,
            "count": len(instruments),
        }
    finally:
        manager.close()


def command_query(scope, args, result: dict) -> None:
    command = args.scpi.strip()
    if not command.endswith("?") or any(
        separator in command for separator in (";", "\r", "\n")
    ):
        raise ValueError(
            "query accepts one SCPI query ending in '?' and no command separators"
        )
    result["command"] = command
    result["response"] = scope.query(command).strip()


def command_measure(scope, adapter, args, result: dict) -> None:
    items = [item.strip() for item in args.items.split(",") if item.strip()]
    if not items:
        raise ValueError("--items must contain at least one measurement")
    result["channel"] = args.channel
    result["measurements"] = adapter.measure(scope, args.channel, items)
    result["scope_error"] = adapter.next_error(scope)


def command_monitor(scope, adapter, args, result: dict) -> None:
    items = [item.strip() for item in args.items.split(",") if item.strip()]
    if not items or args.count < 1 or args.interval < 0:
        raise ValueError("monitor requires items, count >= 1 and interval >= 0")
    samples = []
    for index in range(args.count):
        samples.append(
            {
                "index": index,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "measurements": adapter.measure(scope, args.channel, items),
            }
        )
        if index + 1 < args.count:
            time.sleep(args.interval)
    result["channel"] = args.channel
    result["samples"] = samples
    result["scope_error"] = adapter.next_error(scope)


def command_screenshot(scope, adapter, args, result: dict) -> None:
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result["bytes"] = adapter.screenshot(scope, output)
    result["output"] = str(output)
    result["scope_error"] = adapter.next_error(scope)


def command_waveform(scope, adapter, args, result: dict) -> None:
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result.update(adapter.waveform(scope, args.channel, args.points, output))
    result["channel"] = args.channel
    result["output"] = str(output)
    result["scope_error"] = adapter.next_error(scope)


def command_errors(scope, adapter, _args, result: dict) -> None:
    result["scope_error"] = adapter.next_error(scope)


def command_snapshot(scope, adapter, args, result: dict) -> None:
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result["snapshot_schema"] = adapter.snapshot_schema
    result["settings"] = adapter.snapshot(scope)
    result["scope_error"] = adapter.next_error(scope)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result["output"] = str(output)


def command_restore(scope, adapter, args, result: dict) -> None:
    if not args.confirm_write:
        raise ValueError("restore requires --confirm-write")
    snapshot = json.loads(Path(args.input).expanduser().read_text(encoding="utf-8"))
    if (
        snapshot.get("idn") != result["idn"]
        or snapshot.get("resource") != result["resource"]
    ):
        raise ValueError("snapshot identity does not match the connected instrument")
    if snapshot.get("snapshot_schema") != adapter.snapshot_schema:
        raise ValueError(f"snapshot schema must be {adapter.snapshot_schema!r}")
    settings = snapshot.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise ValueError("snapshot settings must be a non-empty object")
    adapter.restore(scope, settings)
    result["restored"] = True
    result["scope_error"] = adapter.next_error(scope)


ADAPTER_COMMANDS = {
    "measure": command_measure,
    "monitor": command_monitor,
    "screenshot": command_screenshot,
    "waveform": command_waveform,
    "errors": command_errors,
    "snapshot": command_snapshot,
    "restore": command_restore,
}


def run_connected(args) -> dict:
    resource = args.resource or os.environ.get("OSCILLOSCOPE_RESOURCE")
    if not resource:
        raise ValueError("set --resource or OSCILLOSCOPE_RESOURCE")
    manager = open_manager(args.backend)
    scope = None
    try:
        scope = connect(manager, resource, args.timeout_ms)
        idn, adapter = identify(scope)
        result = base_result(resource, idn, adapter)

        if args.command == "identify":
            return result
        if args.command == "query":
            command_query(scope, args, result)
            return result

        adapter = require_adapter(idn, adapter)
        ADAPTER_COMMANDS[args.command](scope, adapter, args, result)
        return result
    finally:
        if scope is not None:
            scope.close()
        manager.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resource", help="VISA resource; defaults to OSCILLOSCOPE_RESOURCE"
    )
    parser.add_argument("--backend", help="PyVISA backend, for example @py")
    parser.add_argument("--timeout-ms", type=positive_int, default=10_000)
    parser.add_argument(
        "--visa-pattern",
        default="USB?*::INSTR",
        help="detect filter; use '?*' only when full LAN/GPIB enumeration is needed",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("detect", help="list VISA resources and query *IDN?")
    subparsers.add_parser("identify", help="identify one instrument and adapter status")
    query = subparsers.add_parser(
        "query", help="send one explicit read-only SCPI query"
    )
    query.add_argument("--scpi", required=True)
    measure = subparsers.add_parser("measure", help="read normalized measurements")
    measure.add_argument("--channel", type=int, default=1)
    measure.add_argument("--items", default="frequency,period,pduty,vpp")
    monitor = subparsers.add_parser("monitor", help="read a finite measurement series")
    monitor.add_argument("--channel", type=int, default=1)
    monitor.add_argument("--items", default="frequency,vpp")
    monitor.add_argument("--count", type=int, default=10)
    monitor.add_argument("--interval", type=float, default=1.0)
    screenshot = subparsers.add_parser("screenshot", help="save a PNG screen capture")
    screenshot.add_argument("--output", required=True)
    waveform = subparsers.add_parser(
        "waveform", help="save calibrated NORMal/BYTE waveform CSV"
    )
    waveform.add_argument("--channel", type=int, default=1)
    waveform.add_argument("--points", type=int, default=1000)
    waveform.add_argument("--output", required=True)
    subparsers.add_parser("errors", help="pop the next instrument error")
    snapshot = subparsers.add_parser(
        "snapshot",
        help="save supported channel, timebase, DVM and edge-trigger settings",
    )
    snapshot.add_argument("--output", required=True)
    restore = subparsers.add_parser("restore", help="restore a matching snapshot")
    restore.add_argument("--input", required=True)
    restore.add_argument("--confirm-write", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = (
            command_detect(args) if args.command == "detect" else run_connected(args)
        )
        emit({"ok": True, **result})
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns structured errors.
        emit(
            {"ok": False, "error": type(exc).__name__, "message": str(exc)},
            stream=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
