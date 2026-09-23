"""Offline cross-repository client -> real Platform auth/HTTP/service journey.

Run in the Platform backend requirements environment with --platform-root set
to a reviewed services/audiences checkout. NocoDB, Superset and DATA completion
are explicit offline fixtures; no network/DATA/provider effects are issued.
This is a cross-layer contract bridge, not a live E2E test.
"""

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform-root", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.platform_root / "audience-workflow" / "backend"))
    from httpx import Response

    from audience_sync.client import AudienceSyncClient, AudienceSyncConfig, SafeApiError
    from audience_sync.project_operations import ProjectOperation as Op
    from tests import test_project_query_execution as execution_fixtures
    from tests.test_project_query_personal_api_journey import ProjectQueryPersonalAPIJourneyTests

    discovery_journeys()
    fixture = ProjectQueryPersonalAPIJourneyTests()
    fixture.setUp()
    calls = []
    superset = fixture.superset

    def superset_with_terminal_member_count(request):
        """Keep the actual mock transport checks, with explicit DATA completion count."""
        response = superset(request)
        if b"COUNT(*) AS member_count" in request.content:
            return Response(200, json={"data": [{"member_count": 17014}]})
        return response

    fixture.superset = superset_with_terminal_member_count

    class Bridge:
        def open(self, request, timeout):
            parsed = urlsplit(request.full_url)
            assert parsed.netloc == "fixture.test"
            response = fixture.http.client.request(
                request.method,
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                content=request.data,
                headers=dict(request.header_items()),
            )
            calls.append((request.method, parsed.path, response.status_code))
            if response.status_code >= 400:
                raise HTTPError(
                    request.full_url, response.status_code, "fixture", {}, BytesIO(response.content)
                )
            return BytesIO(response.content)

    try:
        client = AudienceSyncClient(
            AudienceSyncConfig(
                base_url="https://fixture.test",
                sync_api_key=fixture.key.plaintext,
                contract_revision="audience-project-v3",
                project_id="kiwibit",
            )
        )
        client._opener = Bridge()
        path = {"project_id": "kiwibit"}
        create = fixture.f.create.model_dump(mode="json", exclude_unset=True)
        client.call(Op.VALIDATE_AUDIENCE_QUERY, path=path, body={"criteria": create["criteria"]})
        plan = client.call(Op.CREATE_AUDIENCE_QUERY, path=path, body=create)["resource"]
        path = {**path, "plan_id": plan["plan_id"]}
        assert client.call(Op.GET_AUDIENCE_QUERY, path=path)["resource"] == plan
        preview = client.call(Op.PREVIEW_AUDIENCE_QUERY, path=path)["resource"]
        material = client.call(
            Op.MATERIALIZE_AUDIENCE_QUERY,
            path=path,
            body=fixture.f.materialize_command(preview).model_dump(mode="json"),
        )["resource"]
        fixture.f.succeed(plan["plan_id"])
        materialization_wire = fixture.f.noco.rows[
            "audience_definition_materialization_request_test"
        ][0]
        materialization_wire["member_count"] = 17014
        for table in (
            "audience_materialization_event_test",
            "audience_materialization_result_test",
        ):
            fixture.f.noco.rows[table][0]["member_count"] = 17014
        result = client.call(
            Op.GET_AUDIENCE_QUERY_MATERIALIZATION,
            path={**path, "request_id": material["materialization_request_id"]},
        )["resource"]
        assert (
            result["status"] == "succeeded"
            and result["materialization_run_id"] == "exact-run-123"
            and result["member_count"] == 17014
        )
        destination = client.call(Op.GET_PROJECT_SYNC_CAPABILITIES, path={"project_id": "kiwibit"})[
            "destinations"
        ][0]
        sync = client.call(
            Op.SYNC_AUDIENCE_QUERY,
            path=path,
            body={
                "materialization_request_id": material["materialization_request_id"],
                "materialization_run_id": result["materialization_run_id"],
                "expected_member_count": result["member_count"],
                "destination_id": destination["destination_id"],
                "destination_revision": destination["destination_revision"],
                "confirmed": True,
                "idempotency_key": "canonical-sync-123",
            },
        )["resource"]
        sync_path = {**path, "request_id": sync["sync_request_id"]}
        assert client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=sync_path)["resource"] == sync
        sync_wire = fixture.f.noco.rows["audience_brevo_sync_request_test"][0]
        sync_wire.update(
            status="succeeded",
            dagster_run_id="provider-run-17014",
            list_id="17014",
            completed_at=execution_fixtures.now(),
            added_count=16968,
            removed_count=0,
            missing_alias_count=30,
            malformed_alias_count=1,
            unapproved_domain_count=5,
            duplicate_alias_count=10,
            skipped_count=46,
            safe_error_code="",
        )
        terminal = client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=sync_path)["resource"]
        assert terminal == {
            **sync,
            "status": "succeeded",
            "added_count": 16968,
            "removed_count": 0,
            "missing_alias_count": 30,
            "malformed_alias_count": 1,
            "unapproved_domain_count": 5,
            "duplicate_alias_count": 10,
            "skipped_count": 46,
            "safe_error_code": None,
        }

        materialization_wire.update(status="failed", error_message="not-public")
        failed_materialization = client.call(
            Op.GET_AUDIENCE_QUERY_MATERIALIZATION,
            path={**path, "request_id": material["materialization_request_id"]},
        )["resource"]
        assert failed_materialization["safe_error_code"] == (
            "project_query_materialization_terminal_failure"
        )
        materialization_wire["status"] = "outcome_unknown"
        outcome_unknown = client.call(
            Op.GET_AUDIENCE_QUERY_MATERIALIZATION,
            path={**path, "request_id": material["materialization_request_id"]},
        )["resource"]
        assert outcome_unknown["safe_error_code"] == (
            "project_query_materialization_terminal_failure"
        )

        sync_wire.update(
            status="failed",
            added_count=None,
            removed_count=None,
            missing_alias_count=None,
            malformed_alias_count=None,
            unapproved_domain_count=None,
            duplicate_alias_count=None,
            skipped_count=None,
            safe_error_code="provider_mutation",
        )
        failed_sync = client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=sync_path)["resource"]
        assert failed_sync["safe_error_code"] == "project_query_sync_provider_failed"
        sync_wire.update(status="reconcile_required", safe_error_code="")
        reconcile_required = client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=sync_path)["resource"]
        assert reconcile_required["safe_error_code"] == (
            "project_query_sync_reconciliation_required"
        )
        fixture.key_row["status"] = "revoked"
        before = (len(fixture.f.noco.calls), len(fixture.superset_paths))
        try:
            client.call(Op.GET_QUERY_CAPABILITIES, path={"project_id": "kiwibit"})
        except SafeApiError as exc:
            assert exc.status == 401
        else:
            raise AssertionError("revoked Project key accepted")
        assert before == (len(fixture.f.noco.calls), len(fixture.superset_paths))
        print(
            json.dumps(
                {
                    "status": "passed",
                    "http_calls": len(calls),
                    "project_operations": 13,
                    "revoked_key_denied": True,
                    "terminal_sync_projection": {
                        "added_count": 16968,
                        "skipped_count": 46,
                        "reason_count_sum": 46,
                    },
                    "terminal_safe_failure_readbacks": True,
                    "offline_cross_layer": True,
                    "live_effects": False,
                },
                sort_keys=True,
            )
        )
    finally:
        fixture.doCleanups()


def discovery_journeys():
    """Exercise query forwarding and both discovery scopes with actual Personal auth."""
    from audience_sync.client import AudienceSyncClient, AudienceSyncConfig, SafeApiError
    from audience_sync.project_operations import ProjectOperation as Op
    from tests.test_personal_audience_assets import PersonalAudienceAssetsTests
    from tests.test_personal_query_sync_discovery import PersonalQuerySyncDiscoveryTests

    class Bridge:
        def __init__(self, http):
            self.http, self.calls = http, []

        def open(self, request, timeout):
            parsed = urlsplit(request.full_url)
            assert parsed.netloc == "fixture.test"
            response = self.http.request(
                request.method, parsed.path + ("?" + parsed.query if parsed.query else ""),
                content=request.data, headers=dict(request.header_items()),
            )
            self.calls.append(request)
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "fixture", {},
                                BytesIO(response.content))
            return BytesIO(response.content)

    def client_for(key, http):
        client = AudienceSyncClient(AudienceSyncConfig(
            base_url="https://fixture.test", sync_api_key=key,
            contract_revision="audience-project-v3", project_id="kiwibit",
        ))
        client._opener = Bridge(http)
        return client

    asset = PersonalAudienceAssetsTests()
    asset.setUp()
    try:
        asset.seed(2)
        asset.seed(1, 1)
        asset.seed(1, 1, owner_fingerprint="b" * 64)
        client = client_for(asset.journey.key.plaintext, asset.client)
        query, items = {"limit": "1"}, []
        for _ in range(10):
            result = client.call(Op.LIST_PROJECT_AUDIENCE_ASSETS,
                                 path={"project_id": "kiwibit"}, query=query)["resource"]
            items.extend(result["items"])
            if result["next_cursor"] is None:
                break
            query["cursor"] = result["next_cursor"]
        else:
            raise AssertionError("discovery pagination did not terminate")
        assert len(items) == 3
        assert {item["source"] for item in items} == {"native_audience", "query_plan"}
        assert len(client._opener.calls) > 1
    finally:
        asset.doCleanups()

    sync = PersonalQuerySyncDiscoveryTests()
    sync.setUp()
    try:
        sync.seed()
        sync.j.key_row["owner_fingerprint"] = "b" * 64
        client = client_for(sync.j.key.plaintext, sync.client)
        page = client.call(Op.LIST_PROJECT_SYNCS, path={"project_id": "kiwibit"},
                           query={"status": "succeeded", "limit": "1"})["resource"]
        assert len(page["items"]) == 1
        item = page["items"][0]
        assert item["skipped_count"] == 1
        exact = client.call(Op.GET_PROJECT_SYNC, path={
            "project_id": "kiwibit", "sync_request_id": item["sync_request_id"],
        })["resource"]
        assert exact == item
        assert all(request.method == "GET" and request.data is None
                   for request in client._opener.calls)
        assert sync.client.post(sync.path + "/preview").status_code == 404
        try:
            client.call(Op.LIST_PROJECT_SYNCS, path={"project_id": "other"})
        except SafeApiError as error:
            assert error.code == "project_binding_mismatch"
        else:
            raise AssertionError("cross Project request accepted")
        assert sync.client.get(sync.sync_path.replace("kiwibit", "other")).status_code in {403, 404}
    finally:
        sync.doCleanups()


if __name__ == "__main__":
    main()
