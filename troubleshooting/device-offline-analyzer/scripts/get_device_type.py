from __future__ import annotations

import argparse
import dataclasses
import json
from typing import Optional


@dataclasses.dataclass(frozen=True)
class FirmwareConstraint:
    kind: str
    min_version: Optional[tuple[int, int, int]] = None
    eq_version: Optional[tuple[int, int, int]] = None


@dataclasses.dataclass(frozen=True)
class MatchClause:
    first_prefixes: tuple[str, ...] = ()
    first_equals: tuple[str, ...] = ()
    model_prefixes: tuple[str, ...] = ()
    second_equals: tuple[str, ...] = ()
    second_not_equals: tuple[str, ...] = ()
    first_and_second_equals: tuple[tuple[str, str], ...] = ()
    firmware: FirmwareConstraint = FirmwareConstraint(kind="none")


@dataclasses.dataclass(frozen=True)
class DeviceRule:
    category: str
    sub_category: str
    connection_type: str
    faq_url: str
    clauses: tuple[MatchClause, ...]
    faq_title: str = ""


RULES = [
    DeviceRule(
        faq_title="How to Add Your Plug-in Camera via QR Code",
        category="常电WiFi",
        sub_category="常电单频WiFi扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46947700097561",
        clauses=(
            MatchClause(
                first_prefixes=("CB02", "CB12", "CB22", "CB32", "CB040", "CB140", "CB240", "CB340", "CB061", "CB161", "CB261", "CB027", "CK16", "CL", "CB060C", "CB160C", "CB260C"),
                second_not_equals=("LB", "BD"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
            MatchClause(
                first_equals=("CB0", "CB1", "CB1C", "CB2", "CB060", "CB160", "CB260"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Plug-in Camera via Camera Hotspot",
        category="常电WiFi",
        sub_category="常电单频WiFi单向蓝牙",
        connection_type="单向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46948537667993",
        clauses=(
            MatchClause(
                first_equals=("CB060", "CB160", "CB260", "CB060D", "CB160D", "CB260D", "CK160", "CK160A1", "CK160B"),
                second_not_equals=("LB", "BD"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Plug-in Camera via Bluetooth",
        category="常电WiFi",
        sub_category="常电单频WiFi双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46949131120793",
        clauses=(
            MatchClause(
                first_prefixes=("CB060C", "CB160C", "CB260C", "CB061C", "CB161C", "CB261C", "CL060C", "CL160C", "CK060C1", "CK160C1", "CK060E"),
                second_not_equals=("LB", "BD"),
                firmware=FirmwareConstraint(kind="min", min_version=(1, 9, 4), eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Plug-in Camera via QR Code",
        category="常电WiFi",
        sub_category="常电双频WiFi扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46949436080537",
        clauses=(
            MatchClause(
                first_prefixes=("CB060D1", "CB160D1", "CB260D1", "CB061D1", "CB161D1", "CB261D1", "CL060D"),
                second_not_equals=("LB", "BD"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
            MatchClause(
                first_prefixes=("CB027", "CB127", "CB227"),
                second_not_equals=("LB", "BD"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Plug-in Camera via Bluetooth",
        category="常电WiFi",
        sub_category="常电双频WiFi双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46949625411865",
        clauses=(
            MatchClause(
                first_prefixes=("CB060D1", "CB160D1", "CB260D1", "CB061D1", "CB161D1", "CB261D1", "CL060D"),
                second_not_equals=("LB", "BD"),
                firmware=FirmwareConstraint(kind="min", min_version=(1, 9, 4), eq_version=None),
            ),
            MatchClause(
                first_prefixes=("CB027", "CB127", "CB227"),
                second_not_equals=("LB", "BD"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Camera via Ethernet Cable",
        category="常电网线",
        sub_category="常电网线",
        connection_type="网线",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46950185079961",
        clauses=(
            MatchClause(
                first_prefixes=("CQ2",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Battery Camera via QR Code",
        category="低功耗WiFi",
        sub_category="低功耗WiFi扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46950580231577",
        clauses=(
            MatchClause(
                first_prefixes=("G0", "CG", "CQ1", "CQ4"),
                second_not_equals=("BD1", "BD2"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Battery Camera via Camera Hotspot",
        category="低功耗WiFi",
        sub_category="低功耗WiFi单向蓝牙",
        connection_type="单向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46950720034073",
        clauses=(
            MatchClause(
                first_prefixes=("CG621", "CG623B", "CG623C", "CG623D", "CG623E", "CG623F", "CG1", "CG7", "JS121", "CQ121B", "CQ121C", "CQ121D", "CQ123", "CQ425A1"),
                second_not_equals=("BD1", "BD2"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
            MatchClause(
                first_equals=("CQ425",),
                second_not_equals=("BD1", "BD2"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Battery Camera via Bluetooth",
        category="低功耗WiFi",
        sub_category="低功耗WiFi双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46950876478361",
        clauses=(
            MatchClause(
                first_equals=("CG625",),
                second_not_equals=("BD1", "BD2"),
                firmware=FirmwareConstraint(kind="min", min_version=(1, 7, 5), eq_version=None),
            ),
            MatchClause(
                first_prefixes=("CG623G", "CG623H", "CG625A1", "CG625A2", "CG625B", "CG625D", "CG923"),
                second_not_equals=("BD1", "BD2"),
                firmware=FirmwareConstraint(kind="min", min_version=(1, 7, 5), eq_version=None),
            ),
            MatchClause(
                first_equals=("CQ425A2", "CQ425A3"),
                second_not_equals=("BD1", "BD2"),
                firmware=FirmwareConstraint(kind="eq_or_gte", min_version=(1, 8, 33), eq_version=(1, 8, 27)),
            ),
            MatchClause(
                first_prefixes=("CG625A3", "CG625A4", "CG625E", "CG925"),
                firmware=FirmwareConstraint(kind="eq_or_gte", min_version=(1, 8, 33), eq_version=(1, 8, 27)),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your 4G Battery Camera via QR Code",
        category="低功耗4G",
        sub_category="低功耗4G扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46951199971865",
        clauses=(
            MatchClause(
                model_prefixes=("CQ325",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your 4G Battery Camera via Bluetooth",
        category="低功耗4G",
        sub_category="低功耗4G双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46951375227417",
        clauses=(
            MatchClause(
                model_prefixes=("CQ325",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Light Bulb Camera via QR Code",
        category="WiFi灯泡机",
        sub_category="单频WiFi灯泡机扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46951727386649",
        clauses=(
            MatchClause(
                first_prefixes=("CB02", "CB12", "CB22", "CB32", "CB040", "CB140", "CB240", "CB340", "CB061", "CB161", "CB261", "CB027", "CK16", "CL", "CB060C", "CB160C", "CB260C"),
                second_equals=("LB",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
            MatchClause(
                first_equals=("CB0", "CB1", "CB1C", "CB2", "CB060", "CB160", "CB260"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Light Bulb Camera via Camera Hotspot",
        category="WiFi灯泡机",
        sub_category="单频WiFi灯泡机单向蓝牙",
        connection_type="单向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46952130463769",
        clauses=(
            MatchClause(
                first_equals=("CB060", "CB160", "CB260", "CB060D", "CB160D", "CB260D", "CK160", "CK160A1", "CK160B"),
                second_equals=("-", "LB"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Light Bulb Camera via Bluetooth",
        category="WiFi灯泡机",
        sub_category="单频WiFi灯泡机双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46952273035417",
        clauses=(
            MatchClause(
                first_prefixes=("CB060C", "CB160C", "CB260C", "CB061C", "CB161C", "CB261C", "CL060C", "CL160C", "CK060C1", "CK160C1", "CK060E"),
                firmware=FirmwareConstraint(kind="min", min_version=(1, 9, 4), eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Light Bulb Camera via QR Code",
        category="WiFi灯泡机",
        sub_category="双频WiFi灯泡机扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46952388807449",
        clauses=(
            MatchClause(
                first_prefixes=("CB060D1", "CB160D1", "CB260D1", "CB061D1", "CB161D1", "CB261D1", "CL060D"),
                second_equals=("LB",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
            MatchClause(
                first_prefixes=("CB027", "CB127", "CB227"),
                second_equals=("LB",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Light Bulb Camera via Bluetooth",
        category="WiFi灯泡机",
        sub_category="双频WiFi灯泡机双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46952550484633",
        clauses=(
            MatchClause(
                first_prefixes=("CB060D1", "CB160D1", "CB260D1", "CB061D1", "CB161D1", "CB261D1", "CL060D"),
                second_equals=("LB",),
                firmware=FirmwareConstraint(kind="min", min_version=(1, 9, 4), eq_version=None),
            ),
            MatchClause(
                first_prefixes=("CB027", "CB127", "CB227"),
                second_equals=("LB",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Bird Feeder Camera via QR Code",
        category="喂鸟器",
        sub_category="喂鸟器扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46952837601817",
        clauses=(
            MatchClause(
                first_prefixes=("CG",),
                second_equals=("BD", "BD1", "BD2"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Bird Feeder Camera via Bluetooth",
        category="喂鸟器",
        sub_category="喂鸟器双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46952999477913",
        clauses=(
            MatchClause(
                first_and_second_equals=(("CG625", "BD1"),),
                firmware=FirmwareConstraint(kind="min", min_version=(1, 7, 5), eq_version=None),
            ),
            MatchClause(
                first_and_second_equals=(("CG625", "BD2"), ("CG628", "BD")),
                firmware=FirmwareConstraint(kind="eq_or_gte", min_version=(1, 8, 33), eq_version=(1, 8, 27)),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Bird Feeder Camera via Bluetooth",
        category="喂鸟器",
        sub_category="常电喂鸟器双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/49963477990937",
        clauses=(
            MatchClause(
                first_prefixes=("CK160", "CK127"),
                second_equals=("BD",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Doorbell Camera via QR Code",
        category="门铃",
        sub_category="门铃扫码",
        connection_type="扫码",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46953079522329",
        clauses=(
            MatchClause(
                first_prefixes=("DB",),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Doorbell Camera via Camera Hotspot",
        category="门铃",
        sub_category="门铃单向蓝牙",
        connection_type="单向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46953338624281",
        clauses=(
            MatchClause(
                first_prefixes=("DB1", "DB2"),
                firmware=FirmwareConstraint(kind="none", min_version=None, eq_version=None),
            ),
        ),
    ),
    DeviceRule(
        faq_title="How to Add Your Doorbell Camera via Bluetooth",
        category="门铃",
        sub_category="门铃双向蓝牙",
        connection_type="双向蓝牙",
        faq_url="https://support.vicoo.tech/hc/en-us/articles/46953440662681",
        clauses=(
            MatchClause(
                first_prefixes=("DB3",),
                firmware=FirmwareConstraint(kind="eq_or_gte", min_version=(1, 8, 33), eq_version=(1, 8, 27)),
            ),
        ),
    ),
]


def _get_model_parts(model_str_upper: str) -> tuple[Optional[str], Optional[str]]:
    parts = model_str_upper.split("-")
    first_part = parts[0] if len(parts) > 0 else None
    second_part = parts[1] if len(parts) > 1 else None
    return first_part, second_part


def _parse_version(version_str: Optional[str]) -> Optional[tuple[int, int, int]]:
    if not version_str:
        return None
    parts = [part.strip() for part in str(version_str).split(".") if part.strip()]
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def _firmware_ok(user_fw: Optional[tuple[int, int, int]], fw: FirmwareConstraint) -> bool:
    if fw.kind == "none":
        return True
    if user_fw is None:
        return False
    if fw.kind == "min":
        return fw.min_version is not None and user_fw >= fw.min_version
    if fw.kind == "eq_or_gte":
        if fw.eq_version is None or fw.min_version is None:
            return False
        return user_fw == fw.eq_version or user_fw >= fw.min_version
    return False


def _clause_matches(
    *,
    clause: MatchClause,
    model_upper: str,
    first_part: Optional[str],
    second_part: Optional[str],
) -> bool:
    if clause.model_prefixes and not any(model_upper.startswith(prefix) for prefix in clause.model_prefixes):
        return False
    if clause.first_prefixes:
        if not first_part or not any(first_part.startswith(prefix) for prefix in clause.first_prefixes):
            return False
    if clause.first_equals:
        if not first_part or first_part not in clause.first_equals:
            return False
    if clause.first_and_second_equals:
        if not first_part or not second_part:
            return False
        if (first_part, second_part) not in clause.first_and_second_equals:
            return False
    if clause.second_equals:
        if not second_part or second_part not in clause.second_equals:
            return False
    if clause.second_not_equals and second_part in clause.second_not_equals:
        return False
    return True


def _rule_matches(
    *,
    rule: DeviceRule,
    model_upper: str,
    first_part: Optional[str],
    second_part: Optional[str],
    user_fw: Optional[tuple[int, int, int]],
) -> bool:
    for clause in rule.clauses:
        if not _firmware_ok(user_fw, clause.firmware):
            continue
        if _clause_matches(
            clause=clause,
            model_upper=model_upper,
            first_part=first_part,
            second_part=second_part,
        ):
            return True
    return False


def _get_connection_priority(sub_category_name: str) -> int:
    if "双向蓝牙" in sub_category_name:
        return 3
    if "单向蓝牙" in sub_category_name:
        return 2
    if "扫码" in sub_category_name:
        return 1
    if "网线" in sub_category_name:
        return 0
    return -1


def get_device_type_info(
    model_number: str,
    firmware_version_str: Optional[str],
) -> Optional[dict[str, str]]:
    model_number_upper = model_number.strip().upper()
    if not model_number_upper:
        return None

    first_part, second_part = _get_model_parts(model_number_upper)
    user_fw = _parse_version(firmware_version_str)

    matched_rules = [
        rule
        for rule in RULES
        if _rule_matches(
            rule=rule,
            model_upper=model_number_upper,
            first_part=first_part,
            second_part=second_part,
            user_fw=user_fw,
        )
    ]
    if not matched_rules:
        return None

    best_rule = max(matched_rules, key=lambda rule: _get_connection_priority(rule.sub_category))
    return {
        "model_number": model_number_upper,
        "firmware_version": firmware_version_str or "",
        "category": best_rule.category,
        "sub_category": best_rule.sub_category,
        "connection_type": best_rule.connection_type,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve device type from model number.")
    parser.add_argument("model_number", help="Device model number, such as CB060D1 or CQ325.")
    parser.add_argument("--firmware-version", default=None, help="Optional firmware version, such as 1.9.4.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    result = get_device_type_info(str(args.model_number), args.firmware_version)
    if result is None:
        print("No device type matched.")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"model_number: {result['model_number']}")
        print(f"firmware_version: {result['firmware_version']}")
        print(f"category: {result['category']}")
        print(f"sub_category: {result['sub_category']}")
        print(f"connection_type: {result['connection_type']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
