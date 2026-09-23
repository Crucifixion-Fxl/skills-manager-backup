from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from collect_langfuse_analytics import collect_langfuse_analytics  # noqa: E402


class Identity:
    source = "gitlab"
    user_id = "gitlab-sha256:abc"
    username = "alice"


class Resolver:
    def resolve(self):
        return Identity()


class Source:
    def load(self, **kwargs):
        assert kwargs == {
            "user_id": "gitlab-sha256:abc",
            "since": "2026-09-08T00:00:00Z",
            "until": "2026-09-15T00:00:00Z",
        }
        return {
            "username": "alice",
            "candidate_episodes": 3,
            "episodes": [
                {
                    "production_changed": True,
                    "first_production_change_at": 20,
                    "test_runs": [
                        {
                            "level": "L3",
                            "at": 10,
                            "result": "fail",
                            "test_id": "same-target",
                            "fingerprint": "red",
                        },
                        {
                            "level": "L3",
                            "at": 30,
                            "result": "pass",
                            "test_id": "same-target",
                        },
                    ],
                    "turn_intents": ["plan_approval"],
                    "eval_events": [
                        {"kind": "eval_plan", "at": 5},
                        {"kind": "baseline_or_red", "at": 10},
                        {"kind": "eval_run", "at": 30},
                    ],
                    "eval_classified": True,
                    "work_kinds": ["coding"],
                }
            ]
            * 3,
            "friction": ["test_gap"],
        }


def test_langfuse_collector_uses_gitlab_identity_and_exposes_username():
    report = collect_langfuse_analytics(
        since="2026-09-08",
        until="2026-09-14",
        env={"LANGFUSE_HOST": "http://localhost", "LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"},
        identity_resolver=Resolver(),
        source=Source(),
    )

    assert report["available"] is True
    assert report["user"] == {"id": "gitlab-sha256:abc", "username": "alice"}
    assert report["analytics"]["habits"]["tdd"]["distribution"] == {"red_green": 3}
    assert report["analytics"]["habits"]["eval_driven"]["distribution"] == {
        "baseline_compare": 3
    }
    assert "scores" not in report["analytics"]
    assert "prompt" not in repr(report)


def test_langfuse_collector_requires_service_credentials():
    report = collect_langfuse_analytics(
        since="2026-09-08", until="2026-09-14", env={}, identity_resolver=Resolver()
    )

    assert report == {"available": False, "reason": "langfuse_not_configured"}
