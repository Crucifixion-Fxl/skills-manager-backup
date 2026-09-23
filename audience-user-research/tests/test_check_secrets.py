"""The secret scan matches contiguous credentials and ignores split test fixtures."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _scanner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_secrets.py"
    spec = importlib.util.spec_from_file_location("check_secrets", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("check_secrets_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckSecretsTests(unittest.TestCase):
    def test_split_audience_fixture_is_not_a_secret_and_contiguous_key_is(self) -> None:
        scanner = _scanner()
        fixture = 'KEY = "awpk_v2_" + "a" * 26 + "_" + "B" * 43\n'
        contiguous = "awpk_v2_" + "a" * 26 + "_" + "B" * 43 + "\n"
        self.assertEqual(scanner._matching_rules(fixture.encode()), [])
        self.assertEqual(
            scanner._matching_rules(contiguous.encode()),
            ["audience_personal_key"],
        )

    def test_temporary_aws_key_bearer_and_database_url_match_without_living_in_source(self) -> None:
        scanner = _scanner()
        temporary_key = "ASIA" + "0" * 16
        secret_key = "aws_secret_access_key=" + "a" * 40
        database_url = "postgres://" + "user" + ":" + "secret" + "@db.example/app"
        bearer = "Bearer " + "eyJ" + "a" * 20
        self.assertEqual(
            scanner._matching_rules(temporary_key.encode()),
            ["aws_temporary_access_key"],
        )
        self.assertEqual(
            scanner._matching_rules(secret_key.encode()),
            ["aws_secret_access_key"],
        )
        self.assertEqual(scanner._matching_rules(database_url.encode()), ["database_url"])
        self.assertEqual(scanner._matching_rules(bearer.encode()), ["bearer_token"])
        self.assertEqual(scanner._matching_rules(b"token = rotated-later"), [])


if __name__ == "__main__":
    unittest.main()
