# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Import-only comment reader; use collab.py --help for the public CLI."""
import json
import subprocess
from urllib.parse import quote
from core import record_target


def gitlab_get(host, path):
    try:
        result = subprocess.run(["glab", "api", "--hostname", host, "--method", "GET", path], capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("GitLab 评论读取超时；本轮不推进基线，稍后重读该记录") from error
    if result.returncode:
        raise RuntimeError("GitLab 记录读取失败；检查 glab 登录身份和该 issue 的读取权限")
    return json.loads(result.stdout)


def fetch_comments(record, lark=None, gitlab=gitlab_get):
    host, resource, iid = record_target(record)
    rows = []
    if record["kind"] == "feishu_task":
        token, seen = "", set()
        for _ in range(1000):
            params = {"resource_type": "task", "resource_id": resource, "direction": "asc", "page_size": 100, "user_id_type": "open_id"}
            if token:
                params["page_token"] = token
            page = lark.call(["api", "GET", "/open-apis/task/v2/comments", "--params", json.dumps(params)], "user")
            if not isinstance(page.get("items"), list) or not isinstance(page.get("has_more"), bool):
                raise ValueError("评论分页响应不完整；不会推进基线")
            for c in page["items"]:
                if c.get("resource_id") not in (None, resource) or c.get("resource_type") not in (None, "task"):
                    raise ValueError("评论不属于被跟踪的任务")
                rows.append({"id": str(c["id"]), "body": c["content"], "author": c["creator"]["id"],
                             "version": str(c.get("updated_at") or c.get("created_at") or "unknown")})
            if not page["has_more"]:
                return rows
            token = page.get("page_token")
            if not token or token in seen:
                raise ValueError("评论分页没有前进；不会保存不完整结果")
            seen.add(token)
    else:
        fingerprints = set()
        for page_number in range(1, 1001):
            path = f"projects/{quote(resource, safe='')}/issues/{iid}/notes?page={page_number}&per_page=100&sort=asc&order_by=updated_at"
            page = gitlab(host, path)
            if not isinstance(page, list):
                raise ValueError("GitLab 评论响应必须是列表")
            fingerprint = tuple(str(c.get("id")) for c in page)
            if page and fingerprint in fingerprints:
                raise ValueError("GitLab 重复返回同一页；不会保存不完整结果")
            fingerprints.add(fingerprint)
            for c in page:
                if not c.get("system", False):
                    rows.append({"id": str(c["id"]), "body": c["body"], "author": c["author"]["username"],
                                 "version": str(c.get("updated_at") or c.get("created_at") or "unknown")})
            if len(page) < 100:
                return rows
    raise ValueError("评论数量超过本轮读取限制；不会保存不完整结果")


def poll_record(store, session, record, lark=None, gitlab=gitlab_get):
    if record not in store.watches(session):
        raise ValueError("仅允许读取该 session 已登记的记录")
    comments = fetch_comments(record, lark, gitlab)
    return store.snapshot(session, record["url"], comments)
