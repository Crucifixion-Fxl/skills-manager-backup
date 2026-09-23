#!/usr/bin/env python3
"""alert_correlator.py 单元测试。"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alert_correlator import normalize_title, correlate_incidents, _extract_fault_entity, _normalize_k8s_name, _extract_service_entity


class TestNormalizeTitle:
    """标准化后精确匹配：去掉数字、UUID、Pod 名后缀、时间戳。"""

    def test_strip_numbers(self):
        assert normalize_title("[cn-prod] thanos-query Pod 443258 OOMKilled") == \
               normalize_title("[cn-prod] thanos-query Pod OOMKilled")

    def test_strip_uuid(self):
        assert normalize_title("pod abc123de-f456-7890-abcd-ef1234567890 crashed") == \
               normalize_title("pod crashed")

    def test_strip_pod_suffix(self):
        assert normalize_title("payment-service-7b8f9c6d4f-x2k9z OOMKilled") == \
               normalize_title("payment-service OOMKilled")

    def test_strip_timestamp(self):
        assert normalize_title("alert at 2026-03-24T10:42:50Z") == \
               normalize_title("alert at")

    def test_different_titles_stay_different(self):
        assert normalize_title("[cn-prod] thanos-query OOMKilled") != \
               normalize_title("[eu-prod] payment-service 5xx spike")

    def test_case_insensitive(self):
        assert normalize_title("OOMKilled Alert") == normalize_title("oomkilled alert")


class TestExtractFaultEntity:
    """测试故障实体提取逻辑。"""

    def test_from_alert_details(self):
        """嵌套 labels 格式：details.labels.alertname 等。"""
        inc = {
            "title": "[US] k8s-pod-unhealthy gpu-infer",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-unhealthy",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                }
            },
            "service": {"summary": "prometheus"},
        }
        entity = _extract_fault_entity(inc)
        assert entity == "k8s-pod-unhealthy|us-tech|prod-us"

    def test_from_alert_details_firing_text(self):
        """从 firing 文本解析 labels（原有逻辑回退路径）。"""
        inc = {
            "title": "[US] k8s-pod-unhealthy gpu-infer",
            "alert_details": {
                "firing": "\nLabels:\n - alertname = k8s-pod-unhealthy\n - cluster = us-tech\n - namespace = prod-us\n"
            },
            "service": {"summary": "prometheus"},
        }
        entity = _extract_fault_entity(inc)
        assert entity == "k8s-pod-unhealthy|us-tech|prod-us"

    def test_from_embedded_alerts(self):
        inc = {
            "title": "[GCP] jks-expiring cert warning",
            "alerts": [{
                "body": {
                    "details": {
                        "firing": "\nLabels:\n - alertname = jks-expiring\n - cluster = gcp-tech\n - namespace = prod-gcp\n"
                    }
                }
            }],
            "service": {"summary": "prometheus"},
        }
        entity = _extract_fault_entity(inc)
        assert entity == "jks-expiring|gcp-tech|prod-gcp"

    def test_fallback_to_normalized_title(self):
        inc = {
            "title": "[eu-prod] payment-service 5xx spike",
            "service": {"summary": "payment-service"},
            "alerts": [],
        }
        entity = _extract_fault_entity(inc)
        assert entity == normalize_title("[eu-prod] payment-service 5xx spike")

    def test_alert_details_direct_labels(self):
        inc = {
            "title": "some alert",
            "alert_details": {
                "alertname": "k8s-pod-unhealthy",
                "cluster": "us-tech",
                "namespace": "prod-us",
            },
            "service": {"summary": "prometheus"},
        }
        entity = _extract_fault_entity(inc)
        assert entity == "k8s-pod-unhealthy|us-tech|prod-us"


class TestCorrelateIncidents:
    """测试告警关联逻辑。"""

    @pytest.fixture
    def fixtures_dir(self):
        return os.path.join(os.path.dirname(__file__), "fixtures")

    @pytest.fixture
    def sample_incidents(self, fixtures_dir):
        with open(os.path.join(fixtures_dir, "sample_incidents.json")) as f:
            return json.load(f)["incidents"]

    def test_same_title_within_window_grouped(self):
        """标题标准化后完全相同 + 时间窗口内的告警应被分到同一组。"""
        incidents = [
            {
                "id": "A001",
                "title": "[cn-prod] thanos-query OOMKilled",
                "created_at": "2026-03-24T10:42:50Z",
                "service": {"id": "PSVC001", "summary": "grafana-ai"},
                "alerts": [],
            },
            {
                "id": "A002",
                "title": "[cn-prod] thanos-query OOMKilled",
                "created_at": "2026-03-24T10:43:10Z",
                "service": {"id": "PSVC001", "summary": "grafana-ai"},
                "alerts": [],
            },
        ]
        groups = correlate_incidents(incidents, active_cgs={})
        assert len(groups) == 1
        assert len(groups[0]["incidents"]) == 2

    def test_different_service_separate_groups(self, sample_incidents):
        groups = correlate_incidents(sample_incidents, active_cgs={})
        assert len(groups) >= 2

    def test_title_dedup_merges(self):
        """标题标准化后相同的告警应合并（即使数字不同）。"""
        incidents = [
            {
                "id": "A001",
                "title": "[cn-prod] thanos-query Pod 443258 OOMKilled",
                "created_at": "2026-03-24T10:42:50Z",
                "service": {"id": "PSVC001", "summary": "grafana-ai"},
                "alerts": [],
            },
            {
                "id": "A002",
                "title": "[cn-prod] thanos-query Pod 998877 OOMKilled",
                "created_at": "2026-03-24T10:43:10Z",
                "service": {"id": "PSVC001", "summary": "grafana-ai"},
                "alerts": [],
            },
        ]
        groups = correlate_incidents(incidents, active_cgs={})
        assert len(groups) == 1
        assert len(groups[0]["incidents"]) == 2

    def test_correlate_with_active_cg(self):
        """新告警与 active CG 故障实体匹配时应关联。"""
        new_incidents = [{
            "id": "Q1NEW001",
            "title": "[cn-prod] thanos-query timeout",
            "created_at": "2026-03-24T10:47:00Z",
            "service": {"id": "PSVC001", "summary": "grafana-ai"},
            "alerts": [],
        }]
        active_cgs = {
            "CG-1": {
                "created_at": "2026-03-24T10:44:00Z",
                "service": "grafana-ai",
                "fault_entity": normalize_title("[cn-prod] thanos-query timeout"),
                "environment": "cn-prod",
                "incidents": ["Q1ABC123"],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs=active_cgs)
        assert len(groups) == 1
        assert groups[0]["correlates_to"] == "CG-1"

    def test_group_has_fault_entity_field(self, sample_incidents):
        """每个 group 都应包含 fault_entity 字段。"""
        groups = correlate_incidents(sample_incidents, active_cgs={})
        for g in groups:
            assert "fault_entity" in g
            assert g["fault_entity"] != ""

    def test_group_has_recurrence_of_field(self, sample_incidents):
        """每个 group 都应包含 recurrence_of 字段。"""
        groups = correlate_incidents(sample_incidents, active_cgs={})
        for g in groups:
            assert "recurrence_of" in g

    def test_different_alertname_same_pd_service_not_grouped(self):
        """
        缺陷 1 验证：同一个 PagerDuty service (prometheus) 但不同 alertname
        (jks-expiring vs k8s-pod-unhealthy) 不应被关联到同一组。
        """
        incidents = [
            {
                "id": "JKS001",
                "title": "[GCP] jks-expiring cert warning",
                "created_at": "2026-03-24T10:43:30Z",
                "service": {"id": "PSVC003", "summary": "prometheus"},
                "alerts": [{
                    "body": {
                        "details": {
                            "firing": "\nLabels:\n - alertname = jks-expiring\n - cluster = gcp-tech-service-prometheus\n - namespace = prod-gcp\n"
                        }
                    }
                }],
            },
            {
                "id": "POD001",
                "title": "[US] k8s-pod-unhealthy gpu-infer",
                "created_at": "2026-03-24T10:43:45Z",
                "service": {"id": "PSVC003", "summary": "prometheus"},
                "alerts": [{
                    "body": {
                        "details": {
                            "firing": "\nLabels:\n - alertname = k8s-pod-unhealthy\n - cluster = us-tech-service-prometheus\n - namespace = prod-us\n - pod = gpu-infer-65b75c9b56-2mtbz\n"
                        }
                    }
                }],
            },
        ]
        groups = correlate_incidents(incidents, active_cgs={})
        # 应该产生 2 个独立组，而不是 1 个
        assert len(groups) == 2
        group_ids = {g["group_id"] for g in groups}
        assert len(group_ids) == 2
        # 验证各自的 fault_entity 不同
        entities = {g["fault_entity"] for g in groups}
        assert len(entities) == 2

    def test_recurrence_detection(self):
        """
        缺陷 2 验证：新告警与已完成 CG 的标题标准化匹配时，
        应标记 recurrence_of 为对应 CG ID。
        """
        new_incidents = [{
            "id": "Q2NEW001",
            "title": "[cn-prod] thanos-query OOMKilled",
            "created_at": "2026-03-25T08:00:00Z",
            "service": {"id": "PSVC001", "summary": "grafana-ai"},
            "alerts": [],
        }]
        completed_cgs = {
            "CG-OLD-1": {
                "created_at": "2026-03-24T10:42:50Z",
                "title": "[cn-prod] thanos-query OOMKilled",
                "service": "grafana-ai",
                "fault_entity": normalize_title("[cn-prod] thanos-query OOMKilled"),
                "incidents": [
                    {"id": "Q1ABC123", "title": "[cn-prod] thanos-query OOMKilled"},
                ],
            },
            "CG-OLD-2": {
                "created_at": "2026-03-23T10:00:00Z",
                "title": "[eu-prod] payment-service 5xx spike",
                "service": "payment-service",
                "fault_entity": normalize_title("[eu-prod] payment-service 5xx spike"),
                "incidents": [
                    {"id": "Q1GHI789", "title": "[eu-prod] payment-service 5xx spike"},
                ],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs={}, completed_cgs=completed_cgs)
        assert len(groups) == 1
        assert groups[0]["recurrence_of"] == "CG-OLD-1"

    def test_recurrence_no_match(self):
        """不匹配任何 completed CG 时 recurrence_of 应为 None。"""
        new_incidents = [{
            "id": "Q2NEW002",
            "title": "[cn-prod] something-brand-new error",
            "created_at": "2026-03-25T08:00:00Z",
            "service": {"id": "PSVC001", "summary": "grafana-ai"},
            "alerts": [],
        }]
        completed_cgs = {
            "CG-OLD-1": {
                "title": "[cn-prod] thanos-query OOMKilled",
                "incidents": [
                    {"id": "Q1ABC123", "title": "[cn-prod] thanos-query OOMKilled"},
                ],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs={}, completed_cgs=completed_cgs)
        assert len(groups) == 1
        assert groups[0]["recurrence_of"] is None

    def test_recurrence_matches_via_normalized_title_in_incidents(self):
        """通过 completed CG 内 incidents 列表的标题匹配复发。"""
        new_incidents = [{
            "id": "Q2NEW003",
            "title": "[cn-prod] thanos-query Pod 999999 OOMKilled",
            "created_at": "2026-03-25T08:00:00Z",
            "service": {"id": "PSVC001", "summary": "grafana-ai"},
            "alerts": [],
        }]
        completed_cgs = {
            "CG-OLD-1": {
                "incidents": [
                    {"id": "Q1ABC123", "title": "[cn-prod] thanos-query Pod 443258 OOMKilled"},
                ],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs={}, completed_cgs=completed_cgs)
        assert len(groups) == 1
        # Pod 后缀和数字会被标准化掉，两者标准化后应相同
        assert groups[0]["recurrence_of"] == "CG-OLD-1"


class TestRecurrence2hWindow:
    """复发检测：2h 窗口 + is_recurrence 标记。"""

    def test_recurrence_within_2h(self):
        """completed CG 在 2h 内 → 检测为复发。"""
        new_incidents = [{
            "id": "NEW001",
            "title": "[cn-prod] thanos-query OOMKilled",
            "created_at": "2026-03-25T12:00:00Z",
            "service": {"summary": "grafana-ai"},
            "alerts": [],
        }]
        completed_cgs = {
            "CG-10": {
                "completed_at": "2026-03-25T11:00:00Z",
                "title": "[cn-prod] thanos-query OOMKilled",
                "incidents": [{"id": "OLD001", "title": "[cn-prod] thanos-query OOMKilled"}],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs={}, completed_cgs=completed_cgs)
        assert len(groups) == 1
        assert groups[0]["recurrence_of"] == "CG-10"
        assert groups[0]["is_recurrence"] is True

    def test_recurrence_outside_2h(self):
        """completed CG 超过 2h → 不是复发。"""
        new_incidents = [{
            "id": "NEW002",
            "title": "[cn-prod] thanos-query OOMKilled",
            "created_at": "2026-03-25T16:00:00Z",
            "service": {"summary": "grafana-ai"},
            "alerts": [],
        }]
        completed_cgs = {
            "CG-10": {
                "completed_at": "2026-03-25T11:00:00Z",
                "title": "[cn-prod] thanos-query OOMKilled",
                "incidents": [{"id": "OLD001", "title": "[cn-prod] thanos-query OOMKilled"}],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs={}, completed_cgs=completed_cgs)
        assert len(groups) == 1
        assert groups[0]["recurrence_of"] is None
        assert groups[0].get("is_recurrence", False) is False

    def test_recurrence_no_completed_at_fallback(self):
        """completed CG 无 completed_at → 回退到不限时间匹配。"""
        new_incidents = [{
            "id": "NEW003",
            "title": "[cn-prod] thanos-query OOMKilled",
            "created_at": "2026-03-25T16:00:00Z",
            "service": {"summary": "grafana-ai"},
            "alerts": [],
        }]
        completed_cgs = {
            "CG-10": {
                "title": "[cn-prod] thanos-query OOMKilled",
                "incidents": [{"id": "OLD001", "title": "[cn-prod] thanos-query OOMKilled"}],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs={}, completed_cgs=completed_cgs)
        assert len(groups) == 1
        assert groups[0]["recurrence_of"] == "CG-10"

    def test_is_recurrence_false_when_no_match(self):
        """不匹配任何 CG 时 is_recurrence 为 False。"""
        new_incidents = [{
            "id": "NEW004",
            "title": "[cn-prod] brand-new-alert",
            "created_at": "2026-03-25T12:00:00Z",
            "service": {"summary": "grafana-ai"},
            "alerts": [],
        }]
        groups = correlate_incidents(new_incidents, active_cgs={}, completed_cgs={})
        assert len(groups) == 1
        assert groups[0]["recurrence_of"] is None
        assert groups[0].get("is_recurrence", False) is False


# ─── Task 1 新增测试 ───


class TestNormalizeK8sName:
    """测试 K8s 资源名称标准化：去除 Pod 随机后缀和 ReplicaSet hash。"""

    def test_strip_pod_suffix(self):
        """Pod 名 → Deployment 名: statemachine-7b8f9c6d4f-x2k9z → statemachine"""
        assert _normalize_k8s_name("statemachine-7b8f9c6d4f-x2k9z") == "statemachine"

    def test_strip_replicaset_hash(self):
        """ReplicaSet 名 → Deployment 名: statemachine-7b8f9c6d4f → statemachine"""
        assert _normalize_k8s_name("statemachine-7b8f9c6d4f") == "statemachine"

    def test_strip_statefulset_ordinal(self):
        """StatefulSet Pod 名 → StatefulSet 名: redis-cluster-0 → redis-cluster"""
        assert _normalize_k8s_name("redis-cluster-0") == "redis-cluster"

    def test_strip_statefulset_high_ordinal(self):
        """StatefulSet Pod 高序号: kafka-broker-12 → kafka-broker"""
        assert _normalize_k8s_name("kafka-broker-12") == "kafka-broker"

    def test_no_suffix_unchanged(self):
        """无后缀的名字不变: statemachine → statemachine"""
        assert _normalize_k8s_name("statemachine") == "statemachine"

    def test_short_name_unchanged(self):
        """短名字不变: api → api"""
        assert _normalize_k8s_name("api") == "api"

    def test_name_with_numbers_preserved(self):
        """名字本身含数字不被误剥: gpu-infer-v2 → gpu-infer-v2"""
        assert _normalize_k8s_name("gpu-infer-v2") == "gpu-infer-v2"

    def test_complex_pod_name(self):
        """多层嵌套 Pod 名: my-app-service-5d4f8b7c6-k9x2m → my-app-service"""
        assert _normalize_k8s_name("my-app-service-5d4f8b7c6-k9x2m") == "my-app-service"

    def test_daemonset_pod_name(self):
        """DaemonSet Pod 名: node-exporter-abc12 → node-exporter"""
        assert _normalize_k8s_name("node-exporter-abc12") == "node-exporter"


class TestExtractFaultEntityEnhanced:
    """测试增强后的故障实体提取：从标题/labels 中提取 Deployment/StatefulSet/RDS 等实体名。"""

    def test_extract_deployment_from_labels(self):
        """从 labels 中提取 deployment 名。"""
        inc = {
            "title": "[US] k8s-pod-high-memory statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "deployment": "statemachine",
                }
            },
            "service": {"summary": "prometheus"},
        }
        entity = _extract_fault_entity(inc)
        # 应该包含 deployment 名作为实体标识
        assert "statemachine" in entity

    def test_extract_statefulset_from_labels(self):
        """从 labels 中提取 statefulset 名。"""
        inc = {
            "title": "[US] k8s-pod-restart redis",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "statefulset": "redis-cluster",
                }
            },
            "service": {"summary": "prometheus"},
        }
        entity = _extract_fault_entity(inc)
        assert "redis-cluster" in entity

    def test_extract_rds_from_labels(self):
        """从 labels 中提取 dbidentifier。"""
        inc = {
            "title": "[US] rds-high-cpu main-db",
            "alert_details": {
                "labels": {
                    "alertname": "rds-high-cpu",
                    "dbidentifier": "prod-main-db-001",
                }
            },
            "service": {"summary": "cloudwatch"},
        }
        entity = _extract_fault_entity(inc)
        assert "prod-main-db-001" in entity

    def test_extract_pod_name_normalized_to_deployment(self):
        """从 labels 中的 pod 名提取并标准化为 Deployment 名。"""
        inc = {
            "title": "[US] k8s-pod-restart statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "pod": "statemachine-7b8f9c6d4f-x2k9z",
                }
            },
            "service": {"summary": "prometheus"},
        }
        entity = _extract_fault_entity(inc)
        assert "statemachine" in entity
        # Pod 后缀应该被去除
        assert "7b8f9c6d4f" not in entity
        assert "x2k9z" not in entity

    def test_same_deployment_different_alertnames_same_entity(self):
        """同一 Deployment 不同告警类型应提取相同的故障实体核心标识。"""
        inc_memory = {
            "title": "[US] k8s-pod-high-memory statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "deployment": "statemachine",
                }
            },
            "service": {"summary": "prometheus"},
        }
        inc_restart = {
            "title": "[US] k8s-pod-restart statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "deployment": "statemachine",
                }
            },
            "service": {"summary": "prometheus"},
        }
        entity_memory = _extract_fault_entity(inc_memory)
        entity_restart = _extract_fault_entity(inc_restart)
        # 两者的 service_entity 部分应该相同（都指向 statemachine）
        # 具体格式由实现决定，但核心实体应匹配
        # 注意：alertname 不同所以完整 entity 不同，但 service_entity 应相同
        # 这由 _same_fault_entity_cross_type 判断
        assert "statemachine" in entity_memory
        assert "statemachine" in entity_restart

    def test_extract_opensearch_domain_from_labels(self):
        """从 labels 中提取 OpenSearch domain 名。"""
        inc = {
            "title": "[US] opensearch-high-cpu",
            "alert_details": {
                "labels": {
                    "alertname": "opensearch-high-cpu",
                    "domain_name": "prod-search-001",
                }
            },
            "service": {"summary": "cloudwatch"},
        }
        entity = _extract_fault_entity(inc)
        assert "prod-search-001" in entity


class TestExtractServiceEntity:
    """测试 _extract_service_entity：提取不含 alertname 的 service-level 实体。"""

    def test_returns_deployment_entity(self):
        """有 deployment label 时返回 deployment-based entity。"""
        inc = {
            "title": "[US] k8s-pod-high-memory statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "deployment": "statemachine",
                }
            },
            "service": {"summary": "prometheus"},
        }
        se = _extract_service_entity(inc)
        assert se == "statemachine|us-tech|prod-us"

    def test_returns_statefulset_entity(self):
        """有 statefulset label 时返回 statefulset-based entity。"""
        inc = {
            "title": "[US] k8s-pod-restart redis",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "statefulset": "redis-cluster",
                }
            },
            "service": {"summary": "prometheus"},
        }
        se = _extract_service_entity(inc)
        assert se == "redis-cluster|us-tech|prod-us"

    def test_returns_none_when_no_resource_label(self):
        """无 deployment/statefulset/pod/dbidentifier/domain_name 时返回 None。"""
        inc = {
            "title": "[US] some-alert",
            "alert_details": {
                "labels": {
                    "alertname": "some-alert",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                }
            },
            "service": {"summary": "prometheus"},
        }
        se = _extract_service_entity(inc)
        assert se is None

    def test_returns_none_when_no_labels(self):
        """无任何 labels 时返回 None。"""
        inc = {
            "title": "[US] some-alert",
            "service": {"summary": "grafana-ai"},
            "alerts": [],
        }
        se = _extract_service_entity(inc)
        assert se is None

    def test_pod_normalized_to_deployment(self):
        """Pod 名被标准化后用于 service entity。"""
        inc = {
            "title": "[US] k8s-pod-restart statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "pod": "statemachine-7b8f9c6d4f-x2k9z",
                }
            },
            "service": {"summary": "prometheus"},
        }
        se = _extract_service_entity(inc)
        assert se == "statemachine|us-tech|prod-us"

    def test_pod_and_deployment_labels_give_same_entity(self):
        """pod label 标准化后与 deployment label 提取结果相同。"""
        inc_pod = {
            "title": "[US] k8s-pod-restart statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "pod": "statemachine-7b8f9c6d4f-x2k9z",
                }
            },
            "service": {"summary": "prometheus"},
            "alerts": [],
        }
        inc_deploy = {
            "title": "[US] k8s-pod-high-memory statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "deployment": "statemachine",
                }
            },
            "service": {"summary": "prometheus"},
            "alerts": [],
        }
        se_pod = _extract_service_entity(inc_pod)
        se_deploy = _extract_service_entity(inc_deploy)
        assert se_pod == se_deploy, f"Pod SE '{se_pod}' != Deploy SE '{se_deploy}'"

    def test_rds_dbidentifier_entity(self):
        """RDS dbidentifier 提取为 service entity。"""
        inc = {
            "title": "[US] rds-high-cpu",
            "alert_details": {
                "labels": {
                    "alertname": "rds-high-cpu",
                    "dbidentifier": "prod-main-db-001",
                }
            },
            "service": {"summary": "cloudwatch"},
        }
        se = _extract_service_entity(inc)
        assert se == "prod-main-db-001"

    def test_opensearch_domain_entity(self):
        """OpenSearch domain_name 提取为 service entity。"""
        inc = {
            "title": "[US] opensearch-high-cpu",
            "alert_details": {
                "labels": {
                    "alertname": "opensearch-high-cpu",
                    "domain_name": "prod-search-001",
                }
            },
            "service": {"summary": "cloudwatch"},
        }
        se = _extract_service_entity(inc)
        assert se == "prod-search-001"

    def test_cross_type_same_entity_different_alertnames(self):
        """不同 alertname 但同 deployment → 相同 service entity。"""
        inc_memory = {
            "title": "[US] k8s-pod-high-memory statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "deployment": "statemachine",
                }
            },
            "service": {"summary": "prometheus"},
        }
        inc_restart = {
            "title": "[US] k8s-pod-restart statemachine",
            "alert_details": {
                "labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech",
                    "namespace": "prod-us",
                    "deployment": "statemachine",
                }
            },
            "service": {"summary": "prometheus"},
        }
        assert _extract_service_entity(inc_memory) == _extract_service_entity(inc_restart)


# ─── Task 2 新增测试 ───


class TestCrossTypeMerge:
    """Rule A: 同故障实体、不同告警类型、±30min 内合并。"""

    def test_same_deployment_different_alert_types_merged(self):
        """statemachine k8s-pod-high-memory + k8s-pod-restart within 30min → same group."""
        incidents = [
            {
                "id": "MEM001",
                "title": "[US] k8s-pod-high-memory statemachine",
                "created_at": "2026-03-31T17:03:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
            {
                "id": "RST001",
                "title": "[US] k8s-pod-restart statemachine",
                "created_at": "2026-03-31T17:26:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
        ]
        groups = correlate_incidents(incidents, active_cgs={})
        assert len(groups) == 1
        assert len(groups[0]["incidents"]) == 2

    def test_same_deployment_outside_30min_not_merged(self):
        """Same deployment but >30min apart → separate groups."""
        incidents = [
            {
                "id": "MEM002",
                "title": "[US] k8s-pod-high-memory statemachine",
                "created_at": "2026-03-31T17:00:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
            {
                "id": "RST002",
                "title": "[US] k8s-pod-restart statemachine",
                "created_at": "2026-03-31T18:00:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
        ]
        groups = correlate_incidents(incidents, active_cgs={})
        assert len(groups) == 2

    def test_different_deployments_not_merged(self):
        """Different deployments, same alert type → separate groups."""
        incidents = [
            {
                "id": "MEM003",
                "title": "[US] k8s-pod-high-memory statemachine",
                "created_at": "2026-03-31T17:03:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
            {
                "id": "MEM004",
                "title": "[US] k8s-pod-high-memory middlequery",
                "created_at": "2026-03-31T17:10:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "middlequery",
                }},
                "alerts": [],
            },
        ]
        groups = correlate_incidents(incidents, active_cgs={})
        assert len(groups) == 2

    def test_cross_type_merge_with_active_cg(self):
        """New alert with different type but same deployment as active CG → correlate."""
        new_incidents = [{
            "id": "RST003",
            "title": "[US] k8s-pod-restart statemachine",
            "created_at": "2026-03-31T17:26:00Z",
            "service": {"summary": "prometheus"},
            "alert_details": {"labels": {
                "alertname": "k8s-pod-restart",
                "cluster": "us-tech", "namespace": "prod-us",
                "deployment": "statemachine",
            }},
            "alerts": [],
        }]
        active_cgs = {
            "CG-50": {
                "created_at": "2026-03-31T17:03:00Z",
                "service": "prometheus",
                "fault_entity": "k8s-pod-high-memory|us-tech|prod-us|statemachine",
                "service_entity": "statemachine|us-tech|prod-us",
                "environment": "us-prod",
                "incidents": ["MEM001"],
            }
        }
        groups = correlate_incidents(new_incidents, active_cgs=active_cgs)
        assert len(groups) == 1
        assert groups[0]["correlates_to"] == "CG-50"
