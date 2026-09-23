#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_SOURCE = "NINEDATA_SKILL"
VERSION = "1.2.3"
SQL_TASK_MODULE = "sqlTask"
FAILED_ACTION_CHOICES = ("stop", "ignore", "rollback")
SECRET_KEYS = {"accessKeyId", "accessKeySecret", "signature", "access-key-id"}
AUTH_ENV_VARS = {
    "NINEDATA_API_KEY": "accessKeyId",
    "NINEDATA_SECRET_KEY": "accessKeySecret",
}


def eprint(*args):
    print(*args, file=sys.stderr)


def sanitize(value):
    if isinstance(value, dict):
        return {
            k: ("***" if str(k) in SECRET_KEYS else sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def fail(exit_code, code, message, detail=None, stderr_message=None):
    payload = {"ok": False, "error": {"code": code, "message": message}}
    if detail is not None:
        payload["error"]["detail"] = sanitize(detail)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if stderr_message:
        eprint(stderr_message)
    sys.exit(exit_code)


def resolve_config_path(path):
    if path:
        return Path(path).expanduser()
    if os.environ.get("NINEDATA_SKILL_CONFIG"):
        return Path(os.environ["NINEDATA_SKILL_CONFIG"]).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base_dir = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base_dir / "addx" / "ninedata" / "config.json"


def load_config(path):
    config_path = resolve_config_path(path)
    if not config_path.exists():
        fail(10, "CONFIG_ERROR", f"Configuration file does not exist: {config_path}")
    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
        if mode & 0o077:
            eprint(f"Warning: configuration file should be chmod 600: {config_path}")
    except Exception:
        pass
    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        fail(10, "CONFIG_ERROR", "Configuration file is not valid JSON", str(exc))
    if isinstance(cfg, dict):
        cfg = dict(cfg)
        for env_name, field_name in AUTH_ENV_VARS.items():
            if os.environ.get(env_name):
                cfg[field_name] = os.environ[env_name]
    return cfg, str(config_path)


def get_runtime_config(cfg):
    if not isinstance(cfg, dict):
        fail(10, "CONFIG_ERROR", "Configuration root must be a JSON object")
    required = ["endpoint", "accessKeyId", "accessKeySecret"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        fail(10, "CONFIG_ERROR", f"Configuration is missing required fields: {', '.join(missing)}")
    return cfg


def timestamp_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sign(path, access_key_secret, timestamp):
    message = f"{path}/{access_key_secret}&{timestamp}"
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def request(config, method, path, query=None, body=None, timeout=60):
    endpoint = config["endpoint"].rstrip("/")
    ts = timestamp_utc()
    signature = sign(path, config["accessKeySecret"], ts)

    url = endpoint + path
    if query:
        clean_query = {k: v for k, v in query.items() if v is not None and v != ""}
        if clean_query:
            url += "?" + urllib.parse.urlencode(clean_query)

    headers = {
        "access-key-id": config["accessKeyId"],
        "timestamp": ts,
        "signature": signature,
    }

    data = None
    if method.upper() == "POST":
        headers["Content-Type"] = "application/json"
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        fail(30, "HTTP_ERROR", f"HTTP request failed: {exc.code}", safe_response_text(text))
    except Exception as exc:
        fail(20, "NETWORK_ERROR", "Network request failed", str(exc))

    try:
        return json.loads(text)
    except Exception:
        fail(30, "HTTP_ERROR", "Response is not valid JSON", safe_response_text(text))


def safe_response_text(text):
    try:
        return sanitize(json.loads(text))
    except Exception:
        return text


def print_json(obj):
    print(json.dumps(sanitize(obj), ensure_ascii=False, indent=2))


def require_args(args, names):
    missing = [name for name in names if not getattr(args, name)]
    if missing:
        fail(40, "ARGUMENT_ERROR", "Missing required argument(s): " + ", ".join("--" + name.replace("_", "-") for name in missing))


def require_side_effect_args(args):
    require_args(args, ["reason", "source_client"])
    if not getattr(args, "client_confirmed", False):
        fail(40, "ARGUMENT_ERROR", "Missing --client-confirmed for side-effect SQL Task operation")


def clean_value(value):
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            child = clean_value(v)
            if child is not None and child != "":
                cleaned[k] = child
        return cleaned
    if isinstance(value, list):
        return [clean_value(v) for v in value]
    return value


def sha256_text(text):
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def platform_success(resp):
    return isinstance(resp, dict) and resp.get("success") is True


def platform_data(resp):
    if isinstance(resp, dict):
        return resp.get("data")
    return None


def fail_platform(resp, operation):
    request_id = resp.get("requestId") if isinstance(resp, dict) else None
    message = resp.get("message") if isinstance(resp, dict) else None
    if not message:
        message = "NineData OpenAPI request failed"
    fail(
        50,
        "NINEDATA_ERROR",
        message,
        {"operation": operation, "requestId": request_id},
    )


def request_or_fail(config, method, path, operation, query=None, body=None, timeout=60):
    resp = request(config, method, path, query=query, body=body, timeout=timeout)
    if not platform_success(resp):
        fail_platform(resp, operation)
    return resp


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        fail(40, "ARGUMENT_ERROR", message, stderr_message=self.format_usage().strip())


def validate_config(args):
    cfg, path = load_config(args.config)
    config = get_runtime_config(cfg)
    result = {
        "ok": True,
        "configPath": path,
        "endpoint": config["endpoint"].rstrip("/"),
        "hasAccessKeyId": bool(config.get("accessKeyId")),
        "hasAccessKeySecret": bool(config.get("accessKeySecret")),
        "hasDefaultDsId": bool(config.get("defaultDsId")),
        "defaultDsId": config.get("defaultDsId"),
        "hasDefaultDbName": bool(config.get("defaultDbName")),
        "defaultDbName": config.get("defaultDbName"),
        "hasDefaultSchemaName": bool(config.get("defaultSchemaName")),
        "defaultSchemaName": config.get("defaultSchemaName"),
        "source": config.get("source", DEFAULT_SOURCE),
        "defaultLanguage": config.get("defaultLanguage", "enus"),
        "defaultPageSize": config.get("defaultPageSize", 50),
    }
    if args.check_now:
        result["serverTimeCheck"] = request(config, "GET", "/openapi/now", timeout=args.timeout)
    print_json(result)


def list_datasource(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    query = {
        "datasourceId": args.datasource_id,
        "datasourceType": args.datasource_type,
        "keyword": args.keyword,
        "current": args.current,
        "pageSize": args.page_size,
    }
    print_json(request(config, "GET", "/openapi/v1/datasource/list", query=query, timeout=args.timeout))


def read_text_value(value, file_path, label, required=True):
    if value and file_path:
        fail(40, "ARGUMENT_ERROR", f"Use only one of --{label} and --{label}-file")
    if value:
        return value
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as exc:
            fail(40, "ARGUMENT_ERROR", f"Failed to read {label} file: {file_path}", str(exc))
    if required:
        fail(40, "ARGUMENT_ERROR", f"Missing --{label} or --{label}-file")
    return None


def read_sql(args):
    return read_text_value(args.sql, args.sql_file, "sql", required=True)


def read_optional_sql(args):
    value = read_text_value(args.sql, args.sql_file, "sql", required=False)
    if value is None:
        return None
    value = value.strip()
    if not value:
        fail(40, "ARGUMENT_ERROR", "SQL is empty")
    return value


def read_rollback_sql(args):
    return read_text_value(args.rollback_sql, args.rollback_sql_file, "rollback-sql", required=False)


def sql_execute(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["reason", "source_client"])
    datasource_id = args.datasource_id or config.get("defaultDsId")
    database_name = args.database_name or config.get("defaultDbName")
    schema_name = args.schema_name or config.get("defaultSchemaName")
    sql = read_sql(args).strip()
    if not sql:
        fail(40, "ARGUMENT_ERROR", "SQL is empty")

    body = {
        "datasourceId": datasource_id,
        "dbType": args.db_type,
        "databaseName": database_name,
        "schemaName": schema_name,
        "clusterName": args.cluster_name,
        "sql": sql,
        "current": args.current,
        "pageSize": args.page_size or config.get("defaultPageSize", 50),
        "clientConfirmed": args.client_confirmed,
        "ignoreTips": True,
        "useSessionPersistence": args.use_session_persistence,
        "reason": args.reason,
        "source": config.get("source", DEFAULT_SOURCE),
        "toolId": "sql-execute",
        "sourceClient": args.source_client,
        "sourceSessionId": args.source_session_id,
        "skillVersion": VERSION,
        "language": args.language or config.get("defaultLanguage", "enus"),
    }
    body = clean_value(body)
    if not body.get("datasourceId"):
        fail(40, "ARGUMENT_ERROR", "Missing --datasource-id and config.defaultDsId")
    for key in ("datasourceId", "sql", "reason", "sourceClient"):
        if not body.get(key):
            fail(40, "ARGUMENT_ERROR", f"Missing required request field: {key}")
    print_json(request(config, "POST", "/openapi/v1/sql/execute", body=body, timeout=args.timeout))


def add_audit_fields(body, config, args, tool_id):
    body.update(clean_value({
        "source": config.get("source", DEFAULT_SOURCE),
        "toolId": tool_id,
        "sourceClient": getattr(args, "source_client", None),
        "sourceSessionId": getattr(args, "source_session_id", None),
        "skillVersion": VERSION,
        "language": getattr(args, "language", None) or config.get("defaultLanguage", "enus"),
        "reason": getattr(args, "reason", None),
        "clientConfirmed": getattr(args, "client_confirmed", None),
    }))
    return body


def console_url(config, task_id):
    if not task_id:
        return None
    return config["endpoint"].rstrip("/") + "/dataQuery/task/detail/" + task_id


def infer_next_action(status, approval_candidates=None):
    status = status or ""
    approval_count = len(approval_candidates or [])
    if status in {"preChecking", "ruleChecking"}:
        return "Wait for rule review and query SQL Task detail again."
    if status in {"preCheckFailed", "ruleCheckFailed", "approveRejected", "failed"}:
        return "Review the message, update the SQL Task if needed, or stop the task."
    if status == "ruleCheckSuccess":
        return "Submit the SQL Task for approval."
    if status == "toApprove":
        if approval_count:
            return "Approval is still in progress. If the user has explicitly approved this task, call approve again for the current approval node. Repeat detail or execution-detail after each approval until canExecute=true."
        return "Wait for approval."
    if status == "approved":
        return "Execute the SQL Task after user confirmation."
    if status in {"waiting", "scheduling"}:
        return "Execution is scheduled. Use cancel-execute only if the user requests cancellation."
    if status == "running":
        return "Wait for execution to finish, or suspend/stop if the user requests it."
    if status == "suspended":
        return "Resume or stop the SQL Task."
    if status == "success":
        return "SQL Task completed."
    if status == "stopped":
        return "SQL Task stopped."
    if status == "canceled":
        return "SQL Task canceled."
    if status == "executeCanceled":
        return "Execution was canceled. Execute again if needed."
    return "Query SQL Task detail for the next platform decision."


def normalize_rule_summary(data):
    rule = data.get("ruleCheckResult") if isinstance(data, dict) else None
    if not isinstance(rule, dict):
        return None
    return {
        "errorCount": rule.get("errorCount"),
        "warningCount": rule.get("warningCount"),
        "permissionCount": rule.get("permissionCount"),
        "syntaxCount": rule.get("syntaxCount"),
    }


def current_approval_state(config, task_id, timeout):
    if not task_id:
        return {}
    try:
        resp = node_info_request(config, task_id, timeout)
    except Exception:
        return {}
    if not platform_success(resp):
        return {}
    data = platform_data(resp) or {}
    candidates = normalize_approval_candidates(data)
    state = {
        "approvalRequestId": resp.get("requestId"),
        "approvalCandidates": candidates,
        "approvalRound": len(candidates),
    }
    if len(candidates) == 1:
        state["nextApprovalNodeId"] = candidates[0].get("nodeId")
        state["nextApprovalNodeName"] = candidates[0].get("name")
    return clean_value(state)


def normalize_task_detail_response(resp, config, fallback_task_id=None, operation=None, timeout=60):
    data = platform_data(resp) or {}
    if not isinstance(data, dict):
        data = {}
    biz_data = data.get("bizData") if isinstance(data.get("bizData"), dict) else {}
    task_id = data.get("workflowId") or data.get("id") or fallback_task_id
    status = data.get("status")
    sql_text = biz_data.get("sqlText")
    rollback_text = biz_data.get("rollbackText")
    rule_summary = normalize_rule_summary(data)
    approval_state = current_approval_state(config, task_id, timeout) if status == "toApprove" else {}
    approval_candidates = approval_state.get("approvalCandidates") or []
    normalized = {
        "ok": True,
        "requestId": resp.get("requestId"),
        "taskId": task_id,
        "workflowId": task_id,
        "operation": operation,
        "status": status,
        "statusDesc": data.get("statusDesc"),
        "module": data.get("module") or SQL_TASK_MODULE,
        "datasourceId": data.get("datasourceId") or biz_data.get("datasourceId") or biz_data.get("dsId"),
        "databaseName": data.get("databaseName") or data.get("dbName") or biz_data.get("databaseName") or biz_data.get("dbName"),
        "schemaName": data.get("schemaName") or biz_data.get("schemaName"),
        "ruleCheckSummary": rule_summary,
        "reviewReady": status in {"ruleCheckSuccess", "toApprove", "approved", "waiting", "scheduling", "running", "suspended", "success", "failed", "stopped"},
        "canSubmitApproval": status == "ruleCheckSuccess",
        "canExecute": status == "approved",
        "nextAction": infer_next_action(status, approval_candidates),
        "consoleUrl": console_url(config, task_id),
        "sqlLength": len(sql_text) if sql_text else None,
        "sqlSha256": sha256_text(sql_text) if sql_text else None,
        "hasRollbackSql": bool(rollback_text),
    }
    normalized.update(approval_state)
    return clean_value(normalized)


def normalize_task_list_item(item, config):
    if not isinstance(item, dict):
        return item
    task_id = item.get("workflowId") or item.get("id")
    status = item.get("status")
    return clean_value({
        "taskId": task_id,
        "workflowId": task_id,
        "name": item.get("name"),
        "module": item.get("module"),
        "status": status,
        "datasourceId": item.get("datasourceId"),
        "databaseName": item.get("databaseName") or item.get("dbName"),
        "schemaName": item.get("schemaName"),
        "submitter": item.get("submitter"),
        "createTime": item.get("createTime"),
        "nextAction": infer_next_action(status),
        "consoleUrl": console_url(config, task_id),
    })


def normalize_node(node):
    if not isinstance(node, dict):
        return node
    return clean_value({
        "nodeId": node.get("nodeId") or node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
        "status": node.get("status"),
        "statusDesc": node.get("statusDesc"),
        "createTime": node.get("createTime"),
        "updateTime": node.get("updateTime"),
    })


def normalize_approval_candidates(node_info):
    if not isinstance(node_info, dict):
        return []
    candidates = []
    current_nodes = node_info.get("currentNodes") or []
    pending_statuses = {"toapprove", "to_approve", "waiting"}
    for node in current_nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "").lower()
        status = str(node.get("status") or "").lower()
        if node_type == "approve" and status in pending_statuses:
            candidates.append(normalize_node(node))
    return clean_value(candidates)


def node_info_request(config, task_id, timeout):
    return request_or_fail(
        config,
        "GET",
        "/openapi/v1/workflow/nodeInfo",
        "sql-task-node-info",
        query={"workflowId": task_id},
        timeout=timeout,
    )


def infer_approval_node_id(config, task_id, timeout):
    resp = node_info_request(config, task_id, timeout)
    data = platform_data(resp) or {}
    candidates = normalize_approval_candidates(data)
    if len(candidates) != 1:
        fail(
            45,
            "NODE_INFERENCE_ERROR",
            "Could not uniquely infer the current approval node. Pass --node-id explicitly.",
            {
                "requestId": resp.get("requestId"),
                "taskId": task_id,
                "candidateNodes": candidates,
            },
        )
    return candidates[0].get("nodeId"), resp


def detail_request(config, task_id, timeout):
    return request(config, "GET", "/openapi/v1/workflow/detail", query={"workflowId": task_id}, timeout=timeout)


def operation_result(config, args, operation, task_id, operation_resp):
    detail_resp = detail_request(config, task_id, args.timeout)
    if platform_success(detail_resp):
        normalized = normalize_task_detail_response(detail_resp, config, task_id, operation=operation, timeout=args.timeout)
        normalized["operationRequestId"] = operation_resp.get("requestId")
        return clean_value(normalized)
    return clean_value({
        "ok": True,
        "requestId": operation_resp.get("requestId"),
        "taskId": task_id,
        "workflowId": task_id,
        "operation": operation,
        "accepted": True,
        "nextAction": "Operation succeeded. Query SQL Task detail for the latest status.",
        "consoleUrl": console_url(config, task_id),
    })


def default_task_context(args, config):
    return (
        args.datasource_id or config.get("defaultDsId"),
        args.database_name or config.get("defaultDbName"),
        args.schema_name or config.get("defaultSchemaName"),
    )


def sql_task_submit(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_side_effect_args(args)
    sql = read_sql(args).strip()
    if not sql:
        fail(40, "ARGUMENT_ERROR", "SQL is empty")
    rollback_sql = read_rollback_sql(args)
    datasource_id, database_name, schema_name = default_task_context(args, config)
    if not datasource_id:
        fail(40, "ARGUMENT_ERROR", "Missing --datasource-id and config.defaultDsId")

    biz_data = clean_value({
        "name": args.name or args.reason,
        "datasourceId": datasource_id,
        "dbName": database_name,
        "schemaName": schema_name,
        "sqlInputType": "text",
        "sqlText": sql,
        "rollbackTextType": "text",
        "rollbackText": rollback_sql,
        "comment": args.reason,
        "estimatedAffectedRows": args.estimated_affected_rows,
        "executorType": args.executor_type,
    })
    body = {"module": SQL_TASK_MODULE, "bizData": biz_data}
    add_audit_fields(body, config, args, "sql-task-submit")
    resp = request_or_fail(config, "POST", "/openapi/v1/workflow/create", "sql-task-submit", body=clean_value(body), timeout=args.timeout)
    task_id = platform_data(resp)
    if not task_id:
        fail(50, "NINEDATA_ERROR", "NineData did not return a SQL Task id", {"requestId": resp.get("requestId")})
    print_json(operation_result(config, args, "submit", task_id, resp))


def sql_task_detail(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    resp = request_or_fail(
        config,
        "GET",
        "/openapi/v1/workflow/detail",
        "sql-task-detail",
        query={"workflowId": args.task_id},
        timeout=args.timeout,
    )
    print_json(normalize_task_detail_response(resp, config, args.task_id, operation="detail", timeout=args.timeout))


def sql_task_list(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    query = {
        "module": SQL_TASK_MODULE,
        "datasourceId": args.datasource_id,
        "accountId": args.account_id,
        "accountType": args.account_type,
        "status": args.status,
        "keyword": args.keyword,
        "startTime": args.start_time,
        "endTime": args.end_time,
        "current": args.current,
        "pageSize": args.page_size,
    }
    resp = request_or_fail(config, "GET", "/openapi/v1/workflow/list", "sql-task-list", query=query, timeout=args.timeout)
    tasks = [normalize_task_list_item(item, config) for item in (platform_data(resp) or [])]
    print_json(clean_value({
        "ok": True,
        "requestId": resp.get("requestId"),
        "current": resp.get("current"),
        "pageSize": resp.get("pageSize"),
        "total": resp.get("total"),
        "tasks": tasks,
    }))


def sql_task_log(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    query = {"workflowId": args.task_id, "current": args.current, "pageSize": args.page_size}
    resp = request_or_fail(config, "GET", "/openapi/v1/workflow/log", "sql-task-log", query=query, timeout=args.timeout)
    print_json(clean_value({
        "ok": True,
        "requestId": resp.get("requestId"),
        "taskId": args.task_id,
        "workflowId": args.task_id,
        "current": resp.get("current"),
        "pageSize": resp.get("pageSize"),
        "total": resp.get("total"),
        "logs": platform_data(resp) or [],
        "consoleUrl": console_url(config, args.task_id),
    }))


def build_update_biz_data(args, config, detail_data):
    existing = detail_data.get("bizData") if isinstance(detail_data.get("bizData"), dict) else {}
    sql = read_optional_sql(args) or existing.get("sqlText")
    if not sql:
        fail(40, "ARGUMENT_ERROR", "Missing --sql or --sql-file, and existing SQL Task detail has no sqlText")
    rollback_sql = read_rollback_sql(args)
    datasource_id = args.datasource_id or detail_data.get("datasourceId") or existing.get("datasourceId") or existing.get("dsId") or config.get("defaultDsId")
    database_name = args.database_name or detail_data.get("databaseName") or detail_data.get("dbName") or existing.get("databaseName") or existing.get("dbName") or config.get("defaultDbName")
    schema_name = args.schema_name or detail_data.get("schemaName") or existing.get("schemaName") or config.get("defaultSchemaName")
    return clean_value({
        "name": args.name or existing.get("name") or f"SQL Task {args.task_id}",
        "datasourceId": datasource_id,
        "dbName": database_name,
        "schemaName": schema_name,
        "sqlInputType": "text",
        "sqlText": sql,
        "rollbackTextType": "text",
        "rollbackText": rollback_sql if rollback_sql is not None else existing.get("rollbackText"),
        "comment": args.reason,
        "estimatedAffectedRows": args.estimated_affected_rows if args.estimated_affected_rows is not None else existing.get("estimatedAffectedRows", 0),
        "executorType": args.executor_type or existing.get("executorType") or "creator",
    })


def sql_task_update(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    require_side_effect_args(args)
    detail_resp = request_or_fail(
        config,
        "GET",
        "/openapi/v1/workflow/detail",
        "sql-task-update.detail",
        query={"workflowId": args.task_id},
        timeout=args.timeout,
    )
    detail_data = platform_data(detail_resp) or {}
    body = {
        "workflowId": args.task_id,
        "bizData": build_update_biz_data(args, config, detail_data),
    }
    add_audit_fields(body, config, args, "sql-task-update")
    resp = request_or_fail(config, "POST", "/openapi/v1/workflow/update", "sql-task-update", body=clean_value(body), timeout=args.timeout)
    print_json(operation_result(config, args, "update", args.task_id, resp))


def sql_task_submit_approval(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    require_side_effect_args(args)
    body = {"workflowId": args.task_id}
    add_audit_fields(body, config, args, "sql-task-submit-approval")
    resp = request_or_fail(config, "POST", "/openapi/v1/workflow/submitForApproval", "sql-task-submit-approval", body=clean_value(body), timeout=args.timeout)
    print_json(operation_result(config, args, "submitApproval", args.task_id, resp))


def sql_task_execute(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    require_side_effect_args(args)
    if args.execute_type == "schedule" and not args.execute_time:
        fail(40, "ARGUMENT_ERROR", "Missing --execute-time when --execute-type schedule is used")
    biz_data = clean_value({
        "executeType": "immediately" if args.execute_type == "now" else args.execute_type,
        "executeTime": args.execute_time,
        "execFailedAction": args.exec_failed_action,
        "backupFailedAction": args.backup_failed_action,
    })
    body = {"workflowId": args.task_id, "bizData": biz_data}
    add_audit_fields(body, config, args, "sql-task-execute")
    resp = request_or_fail(config, "POST", "/openapi/v1/workflow/execute", "sql-task-execute", body=clean_value(body), timeout=args.timeout)
    print_json(operation_result(config, args, "execute", args.task_id, resp))


def workflow_operation(args, path, operation, tool_id, cancel_execute=False):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    require_side_effect_args(args)
    if cancel_execute:
        body = {"workflowId": args.task_id, "cancelCause": args.reason}
    else:
        body = {"workflowId": args.task_id, "bizData": {"comment": args.reason}}
    add_audit_fields(body, config, args, tool_id)
    resp = request_or_fail(config, "POST", path, tool_id, body=clean_value(body), timeout=args.timeout)
    print_json(operation_result(config, args, operation, args.task_id, resp))


def sql_task_suspend(args):
    workflow_operation(args, "/openapi/v1/workflow/suspend", "suspend", "sql-task-suspend")


def sql_task_resume(args):
    workflow_operation(args, "/openapi/v1/workflow/resume", "resume", "sql-task-resume")


def sql_task_stop(args):
    workflow_operation(args, "/openapi/v1/workflow/terminate", "stop", "sql-task-stop")


def sql_task_cancel_execute(args):
    workflow_operation(args, "/openapi/v1/workflow/cancelExecute", "cancelExecute", "sql-task-cancel-execute", cancel_execute=True)


def sql_task_cancel(args):
    workflow_operation(args, "/openapi/v1/workflow/cancel", "cancel", "sql-task-cancel")


def sql_task_execution_detail(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    node_resp = request_or_fail(
        config,
        "GET",
        "/openapi/v1/workflow/nodeInfo",
        "sql-task-execution-detail.nodeInfo",
        query={"workflowId": args.task_id},
        timeout=args.timeout,
    )
    subtask_query = {
        "workflowId": args.task_id,
        "nodeId": args.node_id,
        "status": args.status,
        "current": args.current,
        "pageSize": args.page_size,
    }
    subtask_resp = request_or_fail(
        config,
        "GET",
        "/openapi/v1/workflow/subtask/list",
        "sql-task-execution-detail.subtaskList",
        query=clean_value(subtask_query),
        timeout=args.timeout,
    )
    print_json(clean_value({
        "ok": True,
        "requestId": node_resp.get("requestId"),
        "subtaskRequestId": subtask_resp.get("requestId"),
        "taskId": args.task_id,
        "workflowId": args.task_id,
        "operation": "executionDetail",
        "nodes": [normalize_node(node) for node in (platform_data(node_resp) or {}).get("currentNodes", [])],
        "nextNodeName": (platform_data(node_resp) or {}).get("nextNodeName"),
        "nextNodeType": (platform_data(node_resp) or {}).get("nextNodeType"),
        "approvalCandidates": normalize_approval_candidates(platform_data(node_resp) or {}),
        "subtasks": platform_data(subtask_resp) or [],
        "current": subtask_resp.get("current"),
        "pageSize": subtask_resp.get("pageSize"),
        "total": subtask_resp.get("total"),
        "consoleUrl": console_url(config, args.task_id),
    }))


def sql_task_subtask_list(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    query = {
        "workflowId": args.task_id,
        "status": args.status,
        "datasourceId": args.datasource_id,
        "dbName": args.database_name,
        "schemaName": args.schema_name,
        "nodeId": args.node_id,
        "current": args.current,
        "pageSize": args.page_size,
    }
    resp = request_or_fail(config, "GET", "/openapi/v1/workflow/subtask/list", "sql-task-subtask-list", query=clean_value(query), timeout=args.timeout)
    print_json(clean_value({
        "ok": True,
        "requestId": resp.get("requestId"),
        "taskId": args.task_id,
        "workflowId": args.task_id,
        "current": resp.get("current"),
        "pageSize": resp.get("pageSize"),
        "total": resp.get("total"),
        "subtasks": platform_data(resp) or [],
        "consoleUrl": console_url(config, args.task_id),
    }))


def sql_task_subtask_detail(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    query = {
        "workflowId": args.task_id,
        "subTaskId": args.subtask_id,
        "datasourceId": args.datasource_id,
        "dbName": args.database_name,
        "schemaName": args.schema_name,
        "nodeId": args.node_id,
        "includeSql": args.include_sql,
    }
    resp = request_or_fail(config, "GET", "/openapi/v1/workflow/subtask/detail", "sql-task-subtask-detail", query=clean_value(query), timeout=args.timeout)
    print_json(clean_value({
        "ok": True,
        "requestId": resp.get("requestId"),
        "taskId": args.task_id,
        "workflowId": args.task_id,
        "subtaskId": args.subtask_id,
        "includeSql": args.include_sql,
        "details": platform_data(resp) or [],
        "consoleUrl": console_url(config, args.task_id),
    }))


def sql_task_approval_operation(args, approve_result, operation, tool_id):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id"])
    require_side_effect_args(args)
    node_id = args.node_id
    node_info_request_id = None
    if not node_id:
        node_id, node_resp = infer_approval_node_id(config, args.task_id, args.timeout)
        node_info_request_id = node_resp.get("requestId")
    body = {
        "workflowId": args.task_id,
        "nodeId": node_id,
        "approveResult": approve_result,
        "approveMessage": args.reason,
    }
    add_audit_fields(body, config, args, tool_id)
    resp = request_or_fail(config, "POST", "/openapi/v1/workflow/executeApprove", tool_id, body=clean_value(body), timeout=args.timeout)
    result = operation_result(config, args, operation, args.task_id, resp)
    result["nodeId"] = node_id
    result["nodeInfoRequestId"] = node_info_request_id
    print_json(clean_value(result))


def sql_task_approve(args):
    sql_task_approval_operation(args, "approved", "approve", "sql-task-approve")


def sql_task_reject(args):
    sql_task_approval_operation(args, "rejected", "reject", "sql-task-reject")


def sql_task_transfer_approval(args):
    cfg, _ = load_config(args.config)
    config = get_runtime_config(cfg)
    require_args(args, ["task_id", "transfer_account_id"])
    require_side_effect_args(args)
    node_id = args.node_id
    node_info_request_id = None
    if not node_id:
        node_id, node_resp = infer_approval_node_id(config, args.task_id, args.timeout)
        node_info_request_id = node_resp.get("requestId")
    body = {
        "workflowId": args.task_id,
        "nodeId": node_id,
        "transferAccountId": args.transfer_account_id,
    }
    add_audit_fields(body, config, args, "sql-task-transfer-approval")
    resp = request_or_fail(config, "POST", "/openapi/v1/workflow/transferApproval", "sql-task-transfer-approval", body=clean_value(body), timeout=args.timeout)
    result = operation_result(config, args, "transferApproval", args.task_id, resp)
    result["nodeId"] = node_id
    result["nodeInfoRequestId"] = node_info_request_id
    result["transferAccountId"] = args.transfer_account_id
    print_json(clean_value(result))


def add_common_arguments(parser):
    parser.add_argument("--config", default=None)
    return parser


def add_parser(subparsers, name):
    return subparsers.add_parser(name)


def add_source_arguments(parser, side_effect=False):
    parser.add_argument("--source-client")
    parser.add_argument("--source-session-id")
    parser.add_argument("--language")
    if side_effect:
        parser.add_argument("--reason")
        parser.add_argument("--client-confirmed", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def add_task_id_argument(parser):
    parser.add_argument("--task-id", "--workflow-id", dest="task_id")
    return parser


def add_sql_arguments(parser, include_rollback=False):
    parser.add_argument("--sql")
    parser.add_argument("--sql-file")
    if include_rollback:
        parser.add_argument("--rollback-sql")
        parser.add_argument("--rollback-sql-file")
    return parser


def add_sql_task_context_arguments(parser):
    parser.add_argument("--datasource-id")
    parser.add_argument("--database-name")
    parser.add_argument("--schema-name")
    parser.add_argument("--name")
    parser.add_argument("--estimated-affected-rows", type=int, default=0)
    parser.add_argument("--executor-type", default="creator")
    return parser


def main():
    parser = JsonArgumentParser(prog="ninedata")
    sub = parser.add_subparsers(dest="cmd", parser_class=JsonArgumentParser)

    p = add_common_arguments(add_parser(sub, "validate-config"))
    p.add_argument("--check-now", action="store_true")
    p.add_argument("--timeout", type=int, default=20)
    p.set_defaults(func=validate_config)

    p = add_common_arguments(add_parser(sub, "list-datasource"))
    p.add_argument("--datasource-id")
    p.add_argument("--datasource-type")
    p.add_argument("--keyword")
    p.add_argument("--current", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=list_datasource)

    p = add_common_arguments(add_parser(sub, "sql-execute"))
    p.add_argument("--datasource-id")
    p.add_argument("--db-type")
    p.add_argument("--database-name")
    p.add_argument("--schema-name")
    p.add_argument("--cluster-name")
    add_sql_arguments(p)
    p.add_argument("--current", type=int, default=1)
    p.add_argument("--page-size", type=int)
    p.add_argument("--client-confirmed", action="store_true")
    p.add_argument("--use-session-persistence", action="store_true")
    p.add_argument("--reason")
    p.add_argument("--source-client")
    p.add_argument("--source-session-id")
    p.add_argument("--language")
    p.add_argument("--timeout", type=int, default=120)
    p.set_defaults(func=sql_execute)

    p = add_common_arguments(add_parser(sub, "sql-task-submit"))
    add_sql_task_context_arguments(p)
    add_sql_arguments(p, include_rollback=True)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_submit)

    p = add_common_arguments(add_parser(sub, "sql-task-detail"))
    add_task_id_argument(p)
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=sql_task_detail)

    p = add_common_arguments(add_parser(sub, "sql-task-list"))
    p.add_argument("--datasource-id")
    p.add_argument("--account-id")
    p.add_argument("--account-type")
    p.add_argument("--status")
    p.add_argument("--keyword")
    p.add_argument("--start-time")
    p.add_argument("--end-time")
    p.add_argument("--current", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=sql_task_list)

    p = add_common_arguments(add_parser(sub, "sql-task-log"))
    add_task_id_argument(p)
    p.add_argument("--current", type=int, default=1)
    p.add_argument("--page-size", type=int, default=50)
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=sql_task_log)

    p = add_common_arguments(add_parser(sub, "sql-task-update"))
    add_task_id_argument(p)
    add_sql_task_context_arguments(p)
    p.set_defaults(estimated_affected_rows=None, executor_type=None)
    add_sql_arguments(p, include_rollback=True)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_update)

    p = add_common_arguments(add_parser(sub, "sql-task-submit-approval"))
    add_task_id_argument(p)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_submit_approval)

    p = add_common_arguments(add_parser(sub, "sql-task-execute"))
    add_task_id_argument(p)
    p.add_argument("--execute-type", choices=["now", "schedule"], default="now")
    p.add_argument("--execute-time")
    p.add_argument("--exec-failed-action", choices=FAILED_ACTION_CHOICES, default="stop")
    p.add_argument("--backup-failed-action", choices=FAILED_ACTION_CHOICES, default="stop")
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_execute)

    p = add_common_arguments(add_parser(sub, "sql-task-suspend"))
    add_task_id_argument(p)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_suspend)

    p = add_common_arguments(add_parser(sub, "sql-task-resume"))
    add_task_id_argument(p)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_resume)

    p = add_common_arguments(add_parser(sub, "sql-task-stop"))
    add_task_id_argument(p)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_stop)

    p = add_common_arguments(add_parser(sub, "sql-task-cancel-execute"))
    add_task_id_argument(p)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_cancel_execute)

    p = add_common_arguments(add_parser(sub, "sql-task-cancel"))
    add_task_id_argument(p)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_cancel)

    p = add_common_arguments(add_parser(sub, "sql-task-execution-detail"))
    add_task_id_argument(p)
    p.add_argument("--node-id")
    p.add_argument("--status")
    p.add_argument("--current", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=sql_task_execution_detail)

    p = add_common_arguments(add_parser(sub, "sql-task-subtask-list"))
    add_task_id_argument(p)
    p.add_argument("--status")
    p.add_argument("--datasource-id")
    p.add_argument("--database-name")
    p.add_argument("--schema-name")
    p.add_argument("--node-id")
    p.add_argument("--current", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=sql_task_subtask_list)

    p = add_common_arguments(add_parser(sub, "sql-task-subtask-detail"))
    add_task_id_argument(p)
    p.add_argument("--subtask-id")
    p.add_argument("--datasource-id")
    p.add_argument("--database-name")
    p.add_argument("--schema-name")
    p.add_argument("--node-id")
    p.add_argument("--include-sql", action="store_true")
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=sql_task_subtask_detail)

    p = add_common_arguments(add_parser(sub, "sql-task-approve"))
    add_task_id_argument(p)
    p.add_argument("--node-id", type=int)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_approve)

    p = add_common_arguments(add_parser(sub, "sql-task-reject"))
    add_task_id_argument(p)
    p.add_argument("--node-id", type=int)
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_reject)

    p = add_common_arguments(add_parser(sub, "sql-task-transfer-approval"))
    add_task_id_argument(p)
    p.add_argument("--node-id", type=int)
    p.add_argument("--transfer-account-id")
    add_source_arguments(p, side_effect=True)
    p.set_defaults(func=sql_task_transfer_approval)

    args = parser.parse_args()
    if not args.cmd:
        fail(40, "ARGUMENT_ERROR", "Missing command", stderr_message=parser.format_usage().strip())
    args.func(args)


if __name__ == "__main__":
    main()
