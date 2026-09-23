"""Cold-start history reads through the same closed client used by an Agent."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from user_research import AudienceClient, AudienceClientConfig, Operation, SafeApiError

KEY = "awpk_v2_" + "a" * 26 + "_" + "B" * 43
IDEA = "idea_" + "a" * 26
VOC = "ivoc_" + "b" * 26
RESEARCH = "research_" + "c" * 26


def client(*actions: str) -> AudienceClient:
    result = AudienceClient(
        AudienceClientConfig(
            base_url="https://audience-workflow-api-prod-us.addx.live", personal_api_key=KEY
        )
    )
    result._verified_context = {
        "project_id": "kiwibit",
        "binding_revision": "pbr_fixture",
        "allowed_actions": sorted(actions),
    }
    return result


class HistoryMonitorTests(unittest.TestCase):
    def test_history_voc_pages_are_project_bound_and_need_only_read_grant(self) -> None:
        agent = client("voc.results.read")
        first = {
            "project_id": "kiwibit",
            "binding_revision": "pbr_fixture",
            "idea_id": IDEA,
            "kind": "native",
            "offset": 0,
            "limit": 1,
            "has_more": True,
            "items": [
                {
                    "idea_id": IDEA,
                    "resource_id": VOC,
                    "voc_id": VOC,
                    "kind": "native",
                    "dataset_ids": ["dataset-001"],
                }
            ],
        }
        second = {**first, "offset": 1, "has_more": False, "items": []}
        with patch.object(
            agent._opener,
            "open",
            side_effect=[
                io.BytesIO(json.dumps(first).encode()),
                io.BytesIO(json.dumps(second).encode()),
            ],
        ) as opened:
            pages = [
                agent.call(
                    Operation("project_voc_discovery"),
                    path={"idea_id": IDEA},
                    query={"kind": "native", "offset": str(offset), "limit": "1"},
                )
                for offset in (0, 1)
            ]
        self.assertTrue(pages[0]["has_more"])
        self.assertFalse(pages[1]["has_more"])
        self.assertEqual(pages[0]["items"][0]["voc_id"], VOC)
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(
            opened.call_args_list[1].args[0].full_url,
            (
                "https://audience-workflow-api-prod-us.addx.live"
                f"/api/platform/v3/projects/kiwibit/ideas/{IDEA}/voc?kind=native&offset=1&limit=1"
            ),
        )

        foreign = {**first, "project_id": "another-project"}
        with patch.object(
            agent._opener, "open", return_value=io.BytesIO(json.dumps(foreign).encode())
        ):
            with self.assertRaisesRegex(SafeApiError, "invalid_response_schema"):
                agent.call(Operation("project_voc_discovery"), path={"idea_id": IDEA})

    def test_rate_read_works_without_prepare_and_preserves_unknown_denominator(self) -> None:
        agent = client("research.results.read")
        response = {
            "project_id": "kiwibit",
            "binding_revision": "pbr_fixture",
            "research_id": RESEARCH,
            "form_id": "form001",
            "unique_response_count": 3,
            "sent_count": None,
            "response_rate_percent": None,
            "observed_at": "2026-09-17T00:00:00Z",
        }
        with patch.object(
            agent._opener, "open", return_value=io.BytesIO(json.dumps(response).encode())
        ) as opened:
            result = agent.call(
                Operation("personal_research_journey_response_rate"),
                path={"research_id": RESEARCH},
                query={"form_id": "form001"},
            )
        self.assertEqual(result["unique_response_count"], 3)
        self.assertIsNone(result["sent_count"])
        self.assertIsNone(result["response_rate_percent"])
        self.assertEqual(opened.call_count, 1)
        self.assertIn("form_id=form001", opened.call_args.args[0].full_url)

        with patch.object(agent._opener, "open") as opened:
            with self.assertRaisesRegex(SafeApiError, "invalid_query_parameters"):
                agent.call(
                    Operation("personal_research_journey_response_rate"),
                    path={"research_id": RESEARCH},
                )
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
