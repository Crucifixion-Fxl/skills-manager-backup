"""
飞书项目 Open API Client

鉴权：Plugin Token（静默，HTTP API 直调）。
环境变量不完整时实例化会抛出 EnvironmentError，调用方应先用
is_plugin_auth_available() 检查，不可用时走 MCP 路径。

环境变量:
  FEISHU_PLUGIN_ID      - 插件 ID（必需）
  FEISHU_PLUGIN_SECRET   - 插件密钥（必需）
  FEISHU_USER_KEY        - 代理用户 user_key（必需）
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import requests

DEFAULT_TIMEOUT = 30


class FeishuApiError(Exception):
    """飞书项目 API 调用异常（基类）"""


class FeishuAuthError(FeishuApiError):
    """鉴权失败（401 / Plugin Token 过期且无法续期）。捕获后应重新实例化 client。"""


class FeishuTimeoutError(FeishuApiError):
    """HTTP 超时（连接或读响应超过 DEFAULT_TIMEOUT）。业务层可按需重试。"""


def is_plugin_auth_available() -> bool:
    """检查 Plugin Token 鉴权所需的环境变量是否已配置"""
    return all(os.environ.get(k) for k in ("FEISHU_PLUGIN_ID", "FEISHU_PLUGIN_SECRET", "FEISHU_USER_KEY"))


@dataclass
class _TypeCache:
    """单个空间的工作项类型缓存"""
    known_keys: Set[str] = field(default_factory=set)
    api_name_to_key: Dict[str, str] = field(default_factory=dict)


class FeishuProjectClient:
    """飞书项目 API 客户端

    如果环境变量未配置，实例化时会抛出 EnvironmentError 并提示降级到 MCP OAuth。
    调用方可先用 is_plugin_auth_available() 检查。
    """

    def __init__(
        self,
        plugin_id: Optional[str] = None,
        plugin_secret: Optional[str] = None,
        user_key: Optional[str] = None,
        base_url: str = "https://project.feishu.cn/open_api",
    ):
        self.plugin_id = plugin_id or os.environ.get("FEISHU_PLUGIN_ID")
        self.plugin_secret = plugin_secret or os.environ.get("FEISHU_PLUGIN_SECRET")
        self.user_key = user_key or os.environ.get("FEISHU_USER_KEY")
        self.base_url = base_url.rstrip("/")

        if not all([self.plugin_id, self.plugin_secret, self.user_key]):
            missing = [k for k, v in {
                "FEISHU_PLUGIN_ID": self.plugin_id,
                "FEISHU_PLUGIN_SECRET": self.plugin_secret,
                "FEISHU_USER_KEY": self.user_key,
            }.items() if not v]
            raise EnvironmentError(
                f"缺少环境变量: {', '.join(missing)}。"
                f"请配置后重试，或降级使用 MCP OAuth（调用 mcp__feishu-project-mcp__authenticate）。"
            )

        self._token: Optional[str] = None
        self._token_expires_at: float = 0
        self._tenant_token: Optional[str] = None
        self._tenant_token_expires_at: float = 0
        self._type_cache: Dict[str, _TypeCache] = {}

    # ── 鉴权 ──

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        resp = requests.post(
            f"{self.base_url}/authen/plugin_token",
            json={"plugin_id": self.plugin_id, "plugin_secret": self.plugin_secret},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        self._token = data["token"]
        self._token_expires_at = time.time() + data["expire_time"] - 60  # 提前1分钟刷新
        return self._token

    def _resolve_type_key(self, project_key: str, key: str) -> str:
        """将 api_name（如 'feedback'）自动解析为 type_key，已是 type_key 则直接返回"""
        if project_key not in self._type_cache:
            types = self._get(f"/{project_key}/work_item/all-types")
            cache = _TypeCache()
            for t in types:
                cache.known_keys.add(t["type_key"])
                if t.get("api_name"):
                    cache.api_name_to_key[t["api_name"]] = t["type_key"]
            self._type_cache[project_key] = cache
        cache = self._type_cache[project_key]
        if key in cache.known_keys:
            return key
        return cache.api_name_to_key.get(key, key)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-PLUGIN-TOKEN": self._get_token(),
            "X-USER-KEY": self.user_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        try:
            resp = requests.request(method, url, headers=self._headers(), **kwargs)
        except requests.exceptions.Timeout as e:
            raise FeishuTimeoutError(f"Timeout {method} {path}: {e}") from e
        if resp.status_code == 401:
            raise FeishuAuthError(f"HTTP 401 {method} {path}: {resp.text}")
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise FeishuApiError(f"HTTP {resp.status_code} {method} {path}: {body}")
        result = resp.json()
        if result.get("err_code") and result["err_code"] != 0:
            raise FeishuApiError(f"API error {method} {path}: {result}")
        return result.get("data", result)

    def _get(self, path: str, **kwargs) -> Any:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, payload: Any = None, **kwargs) -> Any:
        return self._request("POST", path, json=payload, **kwargs)

    def _put(self, path: str, payload: Any = None, **kwargs) -> Any:
        return self._request("PUT", path, json=payload, **kwargs)

    def _delete(self, path: str, **kwargs) -> Any:
        return self._request("DELETE", path, **kwargs)

    # ── 用户 & 用户组 ──

    def query_user(self, user_keys: List[str]) -> Any:
        """获取用户详情"""
        return self._post("/user/query", {"user_keys": user_keys})

    def search_users(self, query: str, page_size: int = 20) -> Any:
        """模糊搜索租户内用户"""
        return self._post("/user/search", {"query": query, "page_size": page_size})

    def create_user_group(self, project_key: str, name: str, user_keys: List[str]) -> Any:
        """创建自定义用户组"""
        return self._post(f"/{project_key}/user_group", {"name": name, "user_keys": user_keys})

    def update_user_group_members(self, project_key: str, group_id: str, add: List[str] = None, remove: List[str] = None) -> Any:
        """更新用户组成员"""
        payload = {"group_id": group_id}
        if add:
            payload["add_user_keys"] = add
        if remove:
            payload["remove_user_keys"] = remove
        return self._request("PATCH", f"/{project_key}/user_group/members", json=payload)

    def query_user_group_members(self, project_key: str, group_id: str, page_num: int = 1, page_size: int = 20) -> Any:
        """查询用户组成员"""
        return self._post(f"/{project_key}/user_groups/members/page", {
            "group_id": group_id, "page_num": page_num, "page_size": page_size
        })

    # ── 空间 ──

    def get_projects(self, user_key: Optional[str] = None) -> Any:
        """获取空间列表"""
        return self._post("/projects", {"user_key": user_key or self.user_key})

    def get_project_detail(self, project_keys: List[str], user_key: Optional[str] = None) -> Any:
        """获取空间详情"""
        return self._post("/projects/detail", {
            "project_keys": project_keys,
            "user_key": user_key or self.user_key,
        })

    # ── 工作项搜索 ──

    def search_work_items(
        self, project_key: str, work_item_type_keys: List[str],
        page_size: int = 50, page_num: int = 1, **filters
    ) -> Any:
        """搜索工作项（单空间）"""
        work_item_type_keys = [self._resolve_type_key(project_key, k) for k in work_item_type_keys]
        payload = {
            "work_item_type_keys": work_item_type_keys,
            "page_size": page_size,
            "page_num": page_num,
            **filters,
        }
        return self._post(f"/{project_key}/work_item/filter", payload)

    def search_work_items_across(self, project_keys: List[str], work_item_type_key: str, **filters) -> Any:
        """搜索工作项（跨空间）"""
        if project_keys:
            work_item_type_key = self._resolve_type_key(project_keys[0], work_item_type_key)
        payload = {"project_keys": project_keys, "work_item_type_key": work_item_type_key, **filters}
        return self._post("/work_items/filter_across_project", payload)

    def search_work_items_complex(
        self, project_key: str, work_item_type_key: str,
        search_group: Dict, page_size: int = 50, page_num: int = 1
    ) -> Any:
        """搜索工作项（复杂筛选）"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        payload = {"search_group": search_group, "page_size": page_size, "page_num": page_num}
        return self._post(f"/{project_key}/work_item/{work_item_type_key}/search/params", payload)

    def compositive_search(self, query: str, query_type: str = "work_item", **kwargs) -> Any:
        """全局搜索

        Args:
            query_type: 搜索类型（如 work_item）
        """
        return self._post("/compositive_search", {"query": query, "query_type": query_type, **kwargs})

    def search_by_relation(
        self, project_key: str, work_item_type_key: str, work_item_id: str, **kwargs
    ) -> Any:
        """获取关联工作项列表"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/search_by_relation",
            kwargs
        )

    # ── 工作项实例读写 ──

    def get_work_item_detail(self, project_key: str, work_item_type_key: str, work_item_ids: List[int]) -> Any:
        """获取工作项详情"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(f"/{project_key}/work_item/{work_item_type_key}/query", {
            "work_item_ids": work_item_ids
        })

    def get_work_item_meta(self, project_key: str, work_item_type_key: str) -> Any:
        """获取创建工作项元数据"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._get(f"/{project_key}/work_item/{work_item_type_key}/meta")

    def create_work_item(
        self, project_key: str, work_item_type_key: str, fields: List[Dict],
        template_id: Optional[int] = None,
    ) -> Any:
        """创建工作项

        Args:
            template_id: 模板 ID，通过 get_work_item_meta() 获取。部分工作项类型必传。
        """
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        payload = {
            "work_item_type_key": work_item_type_key,
            "fields": fields,
        }
        if template_id is not None:
            payload["template_id"] = template_id
        return self._post(f"/{project_key}/work_item/create", payload)

    def update_work_item(
        self, project_key: str, work_item_type_key: str, work_item_id: str, fields: List[Dict]
    ) -> Any:
        """更新工作项"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._put(f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}", {
            "fields": fields
        })

    def delete_work_item(self, project_key: str, work_item_type_key: str, work_item_id: str) -> Any:
        """删除工作项"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._delete(f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}")

    def abort_work_item(self, project_key: str, work_item_type_key: str, work_item_id: str, abort: bool = True) -> Any:
        """终止/恢复工作项"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._put(f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/abort", {
            "abort": abort
        })

    def get_op_records(self, project_key: str, work_item_ids: List[str], **kwargs) -> Any:
        """获取工作项操作记录"""
        return self._post("/op_record/work_item/list", {
            "project_key": project_key, "work_item_ids": work_item_ids, **kwargs
        })

    def freeze_work_item(self, project_key: str, work_item_type_key: str, work_item_id: str, freeze: bool = True) -> Any:
        """冻结/解冻工作项"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._put("/work_item/freeze", {
            "project_key": project_key,
            "work_item_type_key": work_item_type_key,
            "work_item_id": work_item_id,
            "freeze": freeze,
        })

    # ── 评审 ──

    def batch_query_finished(self, project_key: str, work_item_type_key: str, work_item_id: str, node_ids: List[str]) -> Any:
        """批量查询评审意见、评审结论"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post("/work_item/finished/batch_query", {
            "project_key": project_key,
            "work_item_type_key": work_item_type_key,
            "work_item_id": work_item_id,
            "node_ids": node_ids,
        })

    def update_finished(self, project_key: str, work_item_type_key: str, work_item_id: str, node_id: str, **kwargs) -> Any:
        """修改评审结论和评审意见"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post("/work_item/finished/update", {
            "project_key": project_key,
            "work_item_type_key": work_item_type_key,
            "work_item_id": work_item_id,
            "node_id": node_id,
            **kwargs,
        })

    def query_conclusion_options(self, project_key: str, work_item_type_key: str, node_id: str) -> Any:
        """查询评审结论标签值"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post("/work_item/finished/query_conclusion_option", {
            "project_key": project_key,
            "work_item_type_key": work_item_type_key,
            "node_id": node_id,
        })

    # ── 交付物 ──

    def batch_query_deliverables(self, project_key: str, work_item_ids: List[str]) -> Any:
        """交付物信息批量查询"""
        return self._post("/work_item/deliverable/batch_query", {
            "project_key": project_key, "work_item_ids": work_item_ids
        })

    # ── 工时登记 ──

    def get_man_hour_records(self, project_key: str, work_item_type_key: str, work_item_id: str, **kwargs) -> Any:
        """获取工时登记记录列表"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post("/work_item/man_hour/records", {
            "project_key": project_key,
            "work_item_type_key": work_item_type_key,
            "work_item_id": work_item_id,
            **kwargs,
        })

    def add_man_hour_record(self, project_key: str, work_item_type_key: str, work_item_id: str, records: List[Dict]) -> Any:
        """新增工时登记记录"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/work_hour_record",
            {"records": records}
        )

    def update_man_hour_record(self, project_key: str, work_item_type_key: str, work_item_id: str, records: List[Dict]) -> Any:
        """更新工时登记记录"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._put(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/work_hour_record",
            {"records": records}
        )

    def delete_man_hour_record(self, project_key: str, work_item_type_key: str, work_item_id: str, record_ids: List[str]) -> Any:
        """删除工时登记记录"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._delete(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/work_hour_record",
            json={"record_ids": record_ids}
        )

    # ── 流程与节点 ──

    def get_workflow(self, project_key: str, work_item_type_key: str, work_item_id: str) -> Any:
        """获取工作流详情"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/workflow/query", {})

    def get_wbs_view(self, project_key: str, work_item_type_key: str, work_item_id: str) -> Any:
        """获取工作流详情（WBS）"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._get(f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/wbs_view")

    def update_node(self, project_key: str, work_item_type_key: str, work_item_id: str, node_id: str, **kwargs) -> Any:
        """更新节点（负责人、排期、表单）"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._put(
            f"/{project_key}/workflow/{work_item_type_key}/{work_item_id}/node/{node_id}",
            kwargs
        )

    def node_operate(
        self, project_key: str, work_item_type_key: str, work_item_id: str, node_id: str,
        action: str = "confirm", **kwargs
    ) -> Any:
        """节点完成/回滚"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/workflow/{work_item_type_key}/{work_item_id}/node/{node_id}/operate",
            {"action": action, **kwargs}
        )

    def state_change(
        self, project_key: str, work_item_type_key: str, work_item_id: str, transition_id: str, **kwargs
    ) -> Any:
        """状态流转"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/workflow/{work_item_type_key}/{work_item_id}/node/state_change",
            {"transition_id": transition_id, **kwargs}
        )

    def get_transition_required(self, project_key: str, work_item_type_key: str, work_item_id: str, state_key: str) -> Any:
        """获取流转所需必填信息"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post("/work_item/transition_required_info/get", {
            "project_key": project_key,
            "work_item_type_key": work_item_type_key,
            "work_item_id": work_item_id,
            "state_key": state_key,
        })

    # ── 子任务 ──

    def search_subtasks(self, project_keys: List[str], **kwargs) -> Any:
        """搜索子任务（跨空间）"""
        return self._post("/work_item/subtask/search", {"project_keys": project_keys, **kwargs})

    def get_subtask_detail(self, project_key: str, work_item_type_key: str, work_item_id: str, node_id: str) -> Any:
        """获取子任务详情"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._get(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/workflow/task",
            params={"node_id": node_id}
        )

    def create_subtask(self, project_key: str, work_item_type_key: str, work_item_id: str, node_id: str, **kwargs) -> Any:
        """创建子任务"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/workflow/task",
            {"node_id": node_id, **kwargs}
        )

    def update_subtask(
        self, project_key: str, work_item_type_key: str, work_item_id: str,
        node_id: str, task_id: str, **kwargs
    ) -> Any:
        """更新子任务"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/workflow/{node_id}/task/{task_id}",
            kwargs
        )

    def modify_subtask(self, project_key: str, work_item_type_key: str, work_item_id: str, **kwargs) -> Any:
        """子任务完成/回滚"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/subtask/modify",
            kwargs
        )

    def delete_subtask(self, project_key: str, work_item_type_key: str, work_item_id: str, task_id: str) -> Any:
        """删除子任务"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._delete(f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/task/{task_id}")

    # ── 附件 ──

    def upload_attachment(self, project_key: str, work_item_type_key: str, work_item_id: str, field_key: str, file_path: str) -> Any:
        """添加附件"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        headers = {"X-PLUGIN-TOKEN": self._get_token(), "X-USER-KEY": self.user_key}
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    f"{self.base_url}/{project_key}/work_item/{work_item_type_key}/{work_item_id}/file/upload",
                    headers=headers,
                    data={"field_key": field_key},
                    files={"file": f},
                    timeout=DEFAULT_TIMEOUT,
                )
        except requests.exceptions.Timeout as e:
            raise FeishuTimeoutError(f"Timeout upload attachment: {e}") from e
        if resp.status_code == 401:
            raise FeishuAuthError(f"HTTP 401 upload attachment: {resp.text}")
        if resp.status_code >= 400:
            raise FeishuApiError(f"HTTP {resp.status_code} upload attachment: {resp.text}")
        result = resp.json()
        if result.get("err_code") and result["err_code"] != 0:
            raise FeishuApiError(f"API error upload attachment: {result}")
        return result.get("data", result)

    def upload_file(self, project_key: str, file_path: str) -> Any:
        """上传文件/富文本图片"""
        headers = {"X-PLUGIN-TOKEN": self._get_token(), "X-USER-KEY": self.user_key}
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    f"{self.base_url}/{project_key}/file/upload",
                    headers=headers,
                    files={"file": f},
                    timeout=DEFAULT_TIMEOUT,
                )
        except requests.exceptions.Timeout as e:
            raise FeishuTimeoutError(f"Timeout upload file: {e}") from e
        if resp.status_code == 401:
            raise FeishuAuthError(f"HTTP 401 upload file: {resp.text}")
        if resp.status_code >= 400:
            raise FeishuApiError(f"HTTP {resp.status_code} upload file: {resp.text}")
        result = resp.json()
        if result.get("err_code") and result["err_code"] != 0:
            raise FeishuApiError(f"API error upload file: {result}")
        return result.get("data", result)

    def download_attachment(self, project_key: str, work_item_type_key: str, work_item_id: str, file_key: str) -> bytes:
        """下载附件"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        resp = requests.post(
            f"{self.base_url}/{project_key}/work_item/{work_item_type_key}/{work_item_id}/file/download",
            headers=self._headers(),
            json={"file_key": file_key},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.content

    def delete_attachment(self, project_key: str, work_item_type_key: str, work_item_id: str, file_key: str, field_key: str) -> Any:
        """删除附件"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post("/file/delete", {
            "project_key": project_key,
            "work_item_type_key": work_item_type_key,
            "work_item_id": work_item_id,
            "file_key": file_key,
            "field_key": field_key,
        })

    # ── 空间关联 ──

    def get_relation_rules(self, project_key: str) -> Any:
        """获取空间关联规则列表"""
        return self._post(f"/{project_key}/relation/rules", {})

    def get_relation_work_items(self, project_key: str, work_item_type_key: str, work_item_id: str, **kwargs) -> Any:
        """获取空间关联的工作项实例列表"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(
            f"/{project_key}/relation/{work_item_type_key}/{work_item_id}/work_item_list",
            kwargs
        )

    # ── 评论 ──

    def list_comments(self, project_key: str, work_item_type_key: str, work_item_id: str, **kwargs) -> Any:
        """查询工作项评论列表"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._get(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/comments",
            params=kwargs or None,
        )

    def add_comment(self, project_key: str, work_item_type_key: str, work_item_id: str, content: str, rich_text: Optional[List[Dict]] = None) -> Any:
        """添加评论

        Args:
            content: 纯文本评论内容（与 rich_text 二选一，都有值时 rich_text 优先）
            rich_text: 富文本格式评论内容
        """
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        payload: Dict[str, Any] = {}
        if rich_text is not None:
            payload["rich_text"] = rich_text
        else:
            payload["content"] = content
        return self._post(
            f"/{project_key}/work_item/{work_item_type_key}/{work_item_id}/comment/create",
            payload
        )

    # ── 配置 ──

    def get_work_item_types(self, project_key: str) -> Any:
        """获取空间下的所有工作项类型列表"""
        return self._get(f"/{project_key}/work_item/all-types")

    def get_field_config(self, project_key: str, work_item_type_key: str = None, **kwargs) -> Any:
        """查询字段配置"""
        payload = {**kwargs}
        if work_item_type_key:
            payload["work_item_type_key"] = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(f"/{project_key}/field/all", payload)

    def get_role_config(self, project_key: str, work_item_type_key: str, work_item_id: str) -> Any:
        """查询工作项角色信息"""
        work_item_type_key = self._resolve_type_key(project_key, work_item_type_key)
        return self._post(f"/{project_key}/work_item/{work_item_type_key}/role", {
            "work_item_id": work_item_id,
        })

    # ── 飞书开放平台 API（可选）──

    def get_tenant_access_token(self) -> str:
        """获取飞书开放平台 tenant_access_token（发消息、通讯录等）"""
        if self._tenant_token and time.time() < self._tenant_token_expires_at:
            return self._tenant_token

        app_id = os.environ.get("FEISHU_OPEN_APP_ID")
        app_secret = os.environ.get("FEISHU_OPEN_APP_SECRET")
        if not app_id or not app_secret:
            raise EnvironmentError(
                "缺少环境变量: FEISHU_OPEN_APP_ID 和/或 FEISHU_OPEN_APP_SECRET。"
                "飞书开放平台 API 需要配置这两个环境变量。"
            )
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        self._tenant_token = data["tenant_access_token"]
        self._tenant_token_expires_at = time.time() + data.get("expire", 7200) - 60
        return self._tenant_token
