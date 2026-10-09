"""CN migration must never silently query the wrong account's datasource."""
import contextlib
import importlib.util
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "query_helpers", Path(__file__).resolve().parents[1] / "references/query-helpers.py"
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


class CnQueryRoutingTest(unittest.TestCase):
    def test_unknown_targets_return_errors_without_network_requests(self):
        for query in (helpers.thanos_q, helpers.grafana_q):
            with self.subTest(query=query.__name__), patch.dict(os.environ, {"GRAFANA_TOKEN": "test-only"}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request:
                result = query("unknown-target", "up")
                self.assertIn("unknown target", result["error"])
                self.assertIn("unknown-target", result["error"])
                request.assert_not_called()

    def test_staging_without_endpoint_does_not_contact_prod(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request:
            result = helpers.thanos_q("cn-staging", "up")
        self.assertIn("PROMETHEUS_CN_STAGING_URL", result["error"])
        request.assert_not_called()

    def test_staging_explicit_port_forward_is_used(self):
        with patch.dict(os.environ, {"PROMETHEUS_CN_STAGING_URL": "http://127.0.0.1:18428/"}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b'{"status":"success"}'
            helpers.thanos_q("staging-cn", "up")
        self.assertEqual(request.call_args.args[0].full_url, "http://127.0.0.1:18428/api/v1/query")

    def test_tech_service_without_endpoint_makes_no_network_request(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request:
            result = helpers.thanos_q("cn-tech-service", "up")
        self.assertIn("PROMETHEUS_CN_TECH_SERVICE_URL", result["error"])
        request.assert_not_called()

    def test_tech_service_explicit_base_keeps_vmcluster_tenant_path(self):
        # A mocked endpoint proves URL composition, not deployment readiness.
        base = "https://victoria-metrics-cn-tech-service-tke.addx.live/select/0/prometheus"
        for instant, suffix in ((True, "query"), (False, "query_range")):
            with self.subTest(instant=instant), patch.dict(os.environ, {"PROMETHEUS_CN_TECH_SERVICE_URL": base + "/"}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request:
                request.return_value.__enter__.return_value.read.return_value = b'{"status":"success"}'
                helpers.thanos_q("cn-tech-service", "up", instant=instant, start=1, end=2)
                self.assertEqual(request.call_args.args[0].full_url, base + "/api/v1/" + suffix)

    def test_tech_service_metrics_without_endpoint_makes_no_network_request(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request, contextlib.redirect_stdout(io.StringIO()) as output:
            helpers.cmd_metrics(["metrics", "cn-tech-service-kubelet"])
        self.assertIn("PROMETHEUS_CN_TECH_SERVICE_URL", output.getvalue())
        request.assert_not_called()

    def test_staging_grafana_never_uses_prod_uid(self):
        with patch.dict(os.environ, {"GRAFANA_TOKEN": "test-only"}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request:
            result = helpers.grafana_q("cn-staging", "up")
        self.assertIn("GRAFANA_CN_STAGING_DATASOURCE_UID", result["error"])
        request.assert_not_called()

    def test_explicit_tech_service_grafana_uid_is_used(self):
        with patch.dict(os.environ, {"GRAFANA_TOKEN": "test-only", "GRAFANA_CN_TECH_SERVICE_DATASOURCE_UID": "test-tech"}, clear=True), patch.object(helpers.urllib.request, "urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b'{}'
            helpers.grafana_q("cn-tech-service", "up")
        data = json.loads(request.call_args.args[0].data)
        self.assertEqual(data["queries"][0]["datasource"]["uid"], "test-tech")

    def test_metrics_selects_environment_and_rejects_ambiguous_cn(self):
        for job, target in [("cn-tech-service-kubelet", "cn-tech-service"), ("prod-cn-iot", "cn")]:
            with self.subTest(job=job), patch.object(helpers, "thanos_q", return_value={}) as query, contextlib.redirect_stdout(io.StringIO()):
                helpers.cmd_metrics(["metrics", job])
                self.assertEqual(query.call_args.args[0], target)
        with patch.object(helpers, "thanos_q") as query, contextlib.redirect_stdout(io.StringIO()):
            helpers.cmd_metrics(["metrics", "cn-test-iot"])
        query.assert_not_called()

    def test_staging_job_prefix_requires_explicit_target(self):
        # cn-main still declares these two cn-staging jobs; the prefix does not
        # establish that their metrics moved to account 100052802231.
        for job in ("cn-staging-kiss", "cn-staging-kafka-metrics", "staging-cn-iot"):
            with self.subTest(job=job), patch.object(helpers, "thanos_q") as query, contextlib.redirect_stdout(io.StringIO()) as output:
                helpers.cmd_metrics(["metrics", job])
                query.assert_not_called()
                self.assertIn("explicit target", output.getvalue())

    def test_staging_job_uses_verified_explicit_target(self):
        for target, endpoint in (("cn", "http://127.0.0.1:19090"), ("cn-staging", "http://127.0.0.1:18428")):
            env = {"PROMETHEUS_CN_PROD_URL": "http://127.0.0.1:19090", "PROMETHEUS_CN_STAGING_URL": "http://127.0.0.1:18428"}
            with self.subTest(target=target), patch.dict(os.environ, env, clear=True), patch.object(helpers.urllib.request, "urlopen") as request, contextlib.redirect_stdout(io.StringIO()):
                request.return_value.__enter__.return_value.read.return_value = b'{"status":"success","data":{"result":[]}}'
                helpers.cmd_metrics(["metrics", "cn-staging-kiss", target])
                self.assertEqual(request.call_args.args[0].full_url, endpoint + "/api/v1/query")
                self.assertIn(b"cn-staging-kiss", request.call_args.args[0].data)


if __name__ == "__main__":
    unittest.main()
