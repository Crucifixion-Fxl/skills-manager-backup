#!/usr/bin/env python3
"""
按 device-status-checker Skill 规范查询 Excel 设备绑定和在线状态。

输出特点：
- 一行一设备
- 同一输入行的多设备，使用「输入序号 + 行内设备序号」标识归属
- 未绑定设备在线状态留空
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests


API_ENDPOINTS = {
    "prod-us": "https://troubleshooting-us.addx.live",
    "prod-eu": "https://troubleshooting-eu.addx.live",
}


def now_date_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def start_date_str(days: int = 7) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def split_device_ids(text: str) -> List[str]:
    # 支持英文逗号、中文逗号、空白分隔
    parts = re.split(r"[,\uFF0C\s]+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def identify_device_type(device_id: str) -> Optional[str]:
    value = device_id.strip()
    if len(value) in (14, 15):
        return "user_sn"
    if len(value) == 32 and re.fullmatch(r"[0-9a-fA-F]{32}", value):
        return "device_sn"
    return None


def format_update_time_ms(ms: Optional[int]) -> str:
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms)


def parse_update_time(value: object) -> Tuple[str, Optional[datetime]]:
    if value is None or value == "":
        return "", None

    if isinstance(value, (int, float)):
        try:
            # 兼容毫秒/秒时间戳
            ts = float(value)
            if ts > 1_000_000_000_000:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d %H:%M:%S"), dt
        except Exception:
            return str(value), None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "", None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S"), dt
            except Exception:
                pass
        return text, None

    return str(value), None


@dataclass
class BindingResult:
    binding_status: str
    prod_us_bind_status: str
    prod_eu_bind_status: str
    primary_node: str
    device_model: str
    user_sn: str
    device_sn: str
    error: str


class DeviceStatusChecker:
    def __init__(self, token: str, start_date: str, end_date: str) -> None:
        self.token = token
        self.start_date = start_date
        self.end_date = end_date

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _query_binding_single(self, env: str, id_type: str, id_value: str) -> Dict:
        url = f"{API_ENDPOINTS[env]}/api/v1/log-search/query/db"
        payload = {
            "id_type": id_type,
            "id_value": id_value,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "db_queries": {
                "user_device_binding": ["all"],
                "user_info": ["all"],
            },
            "es_data": {},
            "deidentify": True,
            "encrypted": False,
        }
        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            rows = (
                data.get("result", {})
                .get("data", {})
                .get("results", {})
                .get("UserDeviceBindingQuery", [])
            )
            if not rows:
                return {"success": False, "is_bind": "查询失败", "error": "UserDeviceBindingQuery 为空"}

            factory = rows[0].get("factoryInfoDOS", [])
            if not factory:
                return {"success": False, "is_bind": "查询失败", "error": "factoryInfoDOS 为空"}

            item = factory[0]
            return {
                "success": True,
                "is_bind": item.get("isBind", "查询失败"),
                "device_model": item.get("deviceModel", ""),
                "user_sn": item.get("userSn", ""),
                "device_sn": item.get("deviceSn", ""),
                "error": "",
            }
        except Exception as exc:
            return {"success": False, "is_bind": "查询失败", "error": str(exc)}

    def query_binding(self, device_id: str) -> BindingResult:
        id_type = identify_device_type(device_id)
        if not id_type:
            return BindingResult(
                binding_status="查询失败",
                prod_us_bind_status="查询失败",
                prod_eu_bind_status="查询失败",
                primary_node="",
                device_model="",
                user_sn="",
                device_sn="",
                error=f"无法识别设备标识: {device_id}",
            )

        us = self._query_binding_single("prod-us", id_type, device_id)
        us_bind = us.get("is_bind", "查询失败")

        # Skill 规则：先查 prod-us；未绑定再查 prod-eu；任一已绑定即已绑定
        if us_bind == "1-已绑定":
            return BindingResult(
                binding_status="已绑定",
                prod_us_bind_status=us_bind,
                prod_eu_bind_status="无需查询",
                primary_node="prod-us",
                device_model=us.get("device_model", ""),
                user_sn=us.get("user_sn", ""),
                device_sn=us.get("device_sn", ""),
                error=us.get("error", ""),
            )

        eu = self._query_binding_single("prod-eu", id_type, device_id)
        eu_bind = eu.get("is_bind", "查询失败")

        if eu_bind == "1-已绑定":
            return BindingResult(
                binding_status="已绑定",
                prod_us_bind_status=us_bind,
                prod_eu_bind_status=eu_bind,
                primary_node="prod-eu",
                device_model=eu.get("device_model", "") or us.get("device_model", ""),
                user_sn=eu.get("user_sn", "") or us.get("user_sn", ""),
                device_sn=eu.get("device_sn", "") or us.get("device_sn", ""),
                error=eu.get("error", "") or us.get("error", ""),
            )

        if us_bind == "0-未绑定" and eu_bind == "0-未绑定":
            return BindingResult(
                binding_status="未绑定",
                prod_us_bind_status=us_bind,
                prod_eu_bind_status=eu_bind,
                primary_node="",
                device_model=us.get("device_model", "") or eu.get("device_model", ""),
                user_sn=us.get("user_sn", "") or eu.get("user_sn", ""),
                device_sn=us.get("device_sn", "") or eu.get("device_sn", ""),
                error="",
            )

        return BindingResult(
            binding_status="查询失败",
            prod_us_bind_status=us_bind,
            prod_eu_bind_status=eu_bind,
            primary_node="",
            device_model=us.get("device_model", "") or eu.get("device_model", ""),
            user_sn=us.get("user_sn", "") or eu.get("user_sn", ""),
            device_sn=us.get("device_sn", "") or eu.get("device_sn", ""),
            error=f"US: {us.get('error', '')}; EU: {eu.get('error', '')}".strip("; "),
        )

    def _query_device_status(self, env: str, id_type: str, id_value: str) -> Dict:
        url = f"{API_ENDPOINTS[env]}/api/v1/log-search/query/db"
        payload = {
            "id_type": id_type,
            "id_value": id_value,
            "start_date": self.start_date,
            "end_date": self.end_date,
            # 实测 deviceStatuses 在 user_device_binding 查询结果中返回
            "db_queries": {
                "user_device_binding": ["all"],
                "user_info": ["all"],
            },
            "es_data": {},
            "deidentify": True,
            "encrypted": False,
        }
        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            rows = (
                data.get("result", {})
                .get("data", {})
                .get("results", {})
                .get("UserDeviceBindingQuery", [])
            )
            if not rows:
                return {"success": False, "error": "device status 为空"}

            statuses = rows[0].get("deviceStatuses", [])
            if not statuses:
                return {"success": False, "error": "deviceStatuses 为空"}

            item = statuses[0]
            status_text = item.get("status", "")
            reason = item.get("reason", "")
            update_time_raw = item.get("updateTime")
            update_time_text, update_time_dt = parse_update_time(update_time_raw)
            online_status = "离线"

            if (
                status_text == "已休眠并连上websocket"
                and reason in ("dormantStatus", "mqttreceive")
                and update_time_dt
            ):
                if datetime.now() - update_time_dt < timedelta(hours=1):
                    online_status = "在线"

            return {
                "success": True,
                "online_status": online_status,
                "status": status_text,
                "reason": reason,
                "update_time": update_time_text,
                "error": "",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def query_full(self, device_id: str) -> Dict:
        binding = self.query_binding(device_id)
        input_id_type = identify_device_type(device_id) or ""
        normalized_device_id = device_id.strip()
        device_sn = binding.device_sn.strip()
        if not device_sn and input_id_type == "device_sn":
            device_sn = normalized_device_id

        result = {
            "设备标识": device_id,
            "标识类型": input_id_type,
            "绑定状态": binding.binding_status,
            "prod_us_bind_status": binding.prod_us_bind_status,
            "prod_eu_bind_status": binding.prod_eu_bind_status,
            "设备型号": binding.device_model,
            "user_sn": binding.user_sn,
            "device_sn": device_sn,
            "在线状态": "",
            "状态详情": "",
            "状态原因": "",
            "更新时间": "",
            "错误信息": binding.error,
        }

        # 未绑定设备在线状态留空（Skill 规则）
        if binding.binding_status != "已绑定":
            return result

        status_id_type = input_id_type
        status_id_value = normalized_device_id

        node = binding.primary_node or "prod-us"
        status = self._query_device_status(node, status_id_type, status_id_value)
        if not status.get("success"):
            result["在线状态"] = "查询失败"
            result["错误信息"] = (result["错误信息"] + f"; {status.get('error', '')}").strip("; ")
            return result

        result["在线状态"] = status.get("online_status", "")
        result["状态详情"] = status.get("status", "")
        result["状态原因"] = status.get("reason", "")
        result["更新时间"] = status.get("update_time", "")
        return result


def parse_excel_to_rows(df: pd.DataFrame) -> List[Dict]:
    rows: List[Dict] = []
    for idx, row in df.iterrows():
        source_index = idx + 1
        values = [str(v).strip() for v in row.tolist() if pd.notna(v) and str(v).strip()]
        if not values:
            continue

        joined = ",".join(values)
        device_ids = split_device_ids(joined)
        if not device_ids:
            continue

        # 同一输入行内去重，保留首次出现顺序
        unique_device_ids: List[str] = []
        seen: set[str] = set()
        for device_id in device_ids:
            key = device_id.strip()
            if key and key not in seen:
                seen.add(key)
                unique_device_ids.append(key)

        for inner_idx, device_id in enumerate(unique_device_ids, start=1):
            rows.append(
                {
                    "输入序号": source_index,
                    "行内设备序号": inner_idx,
                    "设备标识": device_id,
                }
            )
    return rows


def run(input_path: str, output_path: str, lookback_days: int = 7) -> None:
    if lookback_days < 1 or lookback_days > 30:
        raise ValueError("lookback-days 必须在 1 到 30 之间")

    token = os.environ.get("TROUBLESHOOTING_TOKEN", "").strip()
    if not token:
        raise RuntimeError("环境变量 TROUBLESHOOTING_TOKEN 未设置")

    # 强制按无表头读取，避免首行设备被当作列名丢失
    df = pd.read_excel(input_path, header=None)
    tasks = parse_excel_to_rows(df)
    checker = DeviceStatusChecker(token, start_date_str(lookback_days), now_date_str())

    records: List[Dict] = []
    total = len(tasks)
    for i, item in enumerate(tasks, start=1):
        device_id = item["设备标识"]
        print(f"[{i}/{total}] 查询设备: {device_id}")
        queried = checker.query_full(device_id)
        records.append(
            {
                "输入序号": item["输入序号"],
                "行内设备序号": item["行内设备序号"],
                **queried,
            }
        )

    output_df = pd.DataFrame(
        records,
        columns=[
            "输入序号",
            "行内设备序号",
            "设备标识",
            "绑定状态",
            "在线状态",
            "错误信息",
        ],
    )
    output_df.to_excel(output_path, index=False)
    print(f"\n查询完成，共 {len(output_df)} 条，输出: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="查询设备绑定状态与在线状态（Excel 批量）")
    parser.add_argument("--input", required=True, help="输入 Excel 路径")
    parser.add_argument("--output", required=True, help="输出 Excel 路径")
    parser.add_argument("--lookback-days", type=int, default=7, help="查询时间范围（天）")
    args = parser.parse_args()

    run(args.input, args.output, args.lookback_days)


if __name__ == "__main__":
    main()
