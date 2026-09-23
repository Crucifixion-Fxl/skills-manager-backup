import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "issue_dedupe.py"
SPEC = importlib.util.spec_from_file_location("issue_dedupe", SCRIPT)
issue_dedupe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_dedupe)


def u(value):
    return value.encode("utf-8").decode("unicode_escape")


class IssueDedupeTest(unittest.TestCase):
    def test_battery_banner_candidate_ranks_above_unrelated_issue(self):
        title = u("\\u7535\\u6c60\\u4fe1\\u606f\\u65e0\\u6cd5\\u83b7\\u53d6\\u7684\\u8b66\\u544a\\u5173\\u95ed\\u540e\\u4e0b\\u6b21\\u4ecd\\u7136\\u5c55\\u793a")
        body = u("\\u7528\\u6237\\u5173\\u95ed\\u540e\\uff0c\\u91cd\\u65b0\\u8fdb\\u5165\\u9875\\u9762\\u4ecd\\u7136\\u5c55\\u793a\\u7535\\u6c60\\u8b66\\u544a\\u6a2a\\u5e45")
        related = {
            "title": u("\\u8bbe\\u5907\\u8bbe\\u7f6e\\u9875\\u7535\\u6c60\\u5f02\\u5e38\\u6a2a\\u5e45\\u624b\\u52a8\\u5173\\u95ed\\u540e\\u91cd\\u8fdb\\u9875\\u9762\\u4ecd\\u5c55\\u793a"),
            "body": "Unable to read battery level. Manual close should persist.",
            "labels": ["enhancement"],
        }
        unrelated = {
            "title": "Alexa snapshot unavailable after sync",
            "body": "Snapshot cache is stale after cloud sync.",
            "labels": ["alexa"],
        }

        related_score = issue_dedupe.score_issue(title, body, related)["score"]
        unrelated_score = issue_dedupe.score_issue(title, body, unrelated)["score"]

        self.assertGreater(related_score, unrelated_score)
        self.assertGreaterEqual(related_score, 0.25)

    def test_default_repositories_include_app_and_iot_targets(self):
        self.assertIn("SWCLIEN/g0-ios", issue_dedupe.DEFAULT_REPOS)
        self.assertIn("SWCLIEN/g0-android", issue_dedupe.DEFAULT_REPOS)
        self.assertIn("CLOUD/iot-service-old", issue_dedupe.DEFAULT_REPOS)
        self.assertIn("CLOUD/iot-service-unified", issue_dedupe.DEFAULT_REPOS)

    def test_default_repositories_include_software_issue_pool(self):
        self.assertIn("issues/software/frontend", issue_dedupe.DEFAULT_REPOS)
        self.assertIn("issues/software/backend", issue_dedupe.DEFAULT_REPOS)
        self.assertIn("issues/software/feedback", issue_dedupe.DEFAULT_REPOS)


def make_match(score: float, state: str):
    return {"score": {"score": score}, "issue": {"state": state}}


class RecommendationTest(unittest.TestCase):
    def test_no_match_recommends_new_issue(self):
        self.assertEqual(issue_dedupe.recommendation(None), "new issue")

    def test_high_score_open_recommends_merge(self):
        self.assertEqual(
            issue_dedupe.recommendation(make_match(0.60, "opened")),
            "merge/update existing",
        )

    def test_high_score_closed_recommends_reopen(self):
        self.assertIn("reopen", issue_dedupe.recommendation(make_match(0.60, "closed")))

    def test_medium_score_open_recommends_compare(self):
        self.assertIn(
            "compare root cause",
            issue_dedupe.recommendation(make_match(0.30, "open")),
        )

    def test_medium_score_closed_recommends_linked_new(self):
        self.assertIn(
            "related link",
            issue_dedupe.recommendation(make_match(0.30, "closed")),
        )

    def test_threshold_boundary_at_0_55_open(self):
        self.assertEqual(
            issue_dedupe.recommendation(make_match(0.55, "open")),
            "merge/update existing",
        )

    def test_threshold_boundary_at_0_25_open(self):
        self.assertIn(
            "compare root cause",
            issue_dedupe.recommendation(make_match(0.25, "open")),
        )

    def test_below_low_threshold_recommends_new_issue(self):
        self.assertEqual(
            issue_dedupe.recommendation(make_match(0.20, "open")),
            "new issue",
        )

    def test_all_failed_returns_insufficient_data_regardless_of_match(self):
        self.assertEqual(
            issue_dedupe.recommendation(None, all_failed=True),
            issue_dedupe.INSUFFICIENT_DATA,
        )
        self.assertEqual(
            issue_dedupe.recommendation(make_match(0.99, "open"), all_failed=True),
            issue_dedupe.INSUFFICIENT_DATA,
        )


if __name__ == "__main__":
    unittest.main()
