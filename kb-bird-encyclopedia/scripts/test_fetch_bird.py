"""Unit tests for fetch_bird.py.

Covers the call paths the AI code review flagged: credential validation,
login failure surface, locale fallback skip, pagination ceiling warning,
and the per-page matcher. All network/IO is mocked — these run offline.

Run from repo root:
    pytest skills/kb-bird-encyclopedia/scripts/test_fetch_bird.py
or via unittest:
    python -m unittest skills.kb-bird-encyclopedia.scripts.test_fetch_bird
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_bird  # noqa: E402


class MatchInPageTests(unittest.TestCase):
    def test_returns_first_substring_hit(self):
        page = [
            {"common_name": "American Robin", "scientific_name": "Turdus migratorius"},
            {"common_name": "Rose-breasted Grosbeak", "scientific_name": "Pheucticus ludovicianus"},
        ]
        hit = fetch_bird._match_in_page(page, "rose-breasted")
        self.assertEqual(hit["common_name"], "Rose-breasted Grosbeak")

    def test_matches_scientific_name(self):
        page = [{"common_name": "Foo", "scientific_name": "Pheucticus ludovicianus"}]
        hit = fetch_bird._match_in_page(page, "ludovicianus")
        self.assertIsNotNone(hit)

    def test_returns_none_when_no_match(self):
        page = [{"common_name": "Bar", "scientific_name": "Sturnus vulgaris"}]
        self.assertIsNone(fetch_bird._match_in_page(page, "robin"))

    def test_tolerates_missing_fields(self):
        page = [{"common_name": None, "scientific_name": None}, {}]
        self.assertIsNone(fetch_bird._match_in_page(page, "anything"))


class FetchLocaleSectionsTests(unittest.TestCase):
    def _log_sink(self):
        captured = []
        return captured, (lambda *a: captured.append(" ".join(str(x) for x in a)))

    def test_returns_sections_when_locale_matches(self):
        captured, log = self._log_sink()
        with mock.patch.object(
            fetch_bird,
            "fetch_content",
            return_value={"locale": "zh", "sections": '{"field_identification": {"title": "x"}}'},
        ):
            out = fetch_bird._fetch_locale_sections("tok", "Sci name", "zh", log)
        self.assertEqual(out, {"field_identification": {"title": "x"}})

    def test_skips_when_backend_falls_back_to_other_locale(self):
        captured, log = self._log_sink()
        with mock.patch.object(
            fetch_bird,
            "fetch_content",
            return_value={"locale": "en", "sections": "{}"},
        ):
            out = fetch_bird._fetch_locale_sections("tok", "Sci name", "ko", log)
        self.assertIsNone(out)
        self.assertTrue(any("backend fell back" in line for line in captured))

    def test_en_request_does_not_check_fallback(self):
        # An en request matched by an en response shouldn't be treated as fallback.
        captured, log = self._log_sink()
        with mock.patch.object(
            fetch_bird,
            "fetch_content",
            return_value={"locale": "en", "sections": '{"k": 1}'},
        ):
            out = fetch_bird._fetch_locale_sections("tok", "Sci", "en", log)
        self.assertEqual(out, {"k": 1})

    def test_returns_none_on_fetch_exception(self):
        captured, log = self._log_sink()
        with mock.patch.object(
            fetch_bird, "fetch_content", side_effect=RuntimeError("boom")
        ):
            out = fetch_bird._fetch_locale_sections("tok", "Sci", "zh", log)
        self.assertIsNone(out)
        self.assertTrue(any("fetch failed" in line for line in captured))

    def test_falls_back_to_raw_on_invalid_json(self):
        captured, log = self._log_sink()
        with mock.patch.object(
            fetch_bird,
            "fetch_content",
            return_value={"locale": "en", "sections": "not-json"},
        ):
            out = fetch_bird._fetch_locale_sections("tok", "Sci", "en", log)
        self.assertEqual(out, {"_raw": "not-json"})


class FindSpeciesPaginationTests(unittest.TestCase):
    def test_returns_first_hit_across_pages(self):
        page1 = [{"common_name": "A", "scientific_name": "Alpha alpha"}]
        page2 = [{"common_name": "Rose-breasted Grosbeak", "scientific_name": "Pheucticus ludovicianus"}]
        responses = [{"species": page1}, {"species": page2}]
        with mock.patch.object(fetch_bird, "http_json", side_effect=responses):
            hit = fetch_bird.find_species("tok", "Rose-breasted", page_size=200)
        self.assertEqual(hit["scientific_name"], "Pheucticus ludovicianus")

    def test_stops_on_empty_page(self):
        with mock.patch.object(fetch_bird, "http_json", return_value={"species": []}):
            self.assertIsNone(fetch_bird.find_species("tok", "anything", page_size=200))

    def test_pagination_ceiling_emits_warning(self):
        # Always return non-empty pages with no match so we exhaust the ceiling.
        non_match_page = {"species": [{"common_name": "x", "scientific_name": "y"}]}
        captured_stderr = io.StringIO()
        with mock.patch.object(fetch_bird, "http_json", return_value=non_match_page), \
                mock.patch.object(sys, "stderr", captured_stderr):
            result = fetch_bird.find_species("tok", "nonexistent-bird", page_size=10000)
        self.assertIsNone(result)
        self.assertIn("scanned", captured_stderr.getvalue())
        self.assertIn("Pagination ceiling reached", captured_stderr.getvalue())


class LoginFailureTests(unittest.TestCase):
    def test_login_raises_on_nonzero_result(self):
        encry_resp = {"data": {"encry": 0}}
        login_resp = {"result": 1, "msg": "invalid credentials"}
        with mock.patch.object(fetch_bird, "http_json", side_effect=[encry_resp, login_resp]):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_bird.login("foo@bar.com", "pw")
        self.assertIn("invalid credentials", str(ctx.exception))

    def test_login_raises_when_token_missing(self):
        encry_resp = {"data": {"encry": 0}}
        login_resp = {"result": 0, "data": {}}
        with mock.patch.object(fetch_bird, "http_json", side_effect=[encry_resp, login_resp]):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_bird.login("foo@bar.com", "pw")
        self.assertIn("no token", str(ctx.exception))


class MainCredentialGateTests(unittest.TestCase):
    def test_main_exits_when_credentials_missing(self):
        # Clear KB_EMAIL/KB_PASSWORD; argv only has --name
        with mock.patch.dict(os.environ, {}, clear=False) as env, \
                mock.patch.object(sys, "argv", ["fetch_bird.py", "--name", "x"]), \
                mock.patch.object(sys, "stderr", io.StringIO()) as err:
            for k in ("KB_EMAIL", "KB_PASSWORD"):
                env.pop(k, None)
            with self.assertRaises(SystemExit) as ctx:
                fetch_bird.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("missing KB credentials", err.getvalue())


class SslContextTests(unittest.TestCase):
    def test_default_is_verified(self):
        with mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("KB_VERIFY_SSL", None)
            ctx = fetch_bird._make_ssl_context()
        # The verified default has hostname check on and CERT_REQUIRED.
        self.assertTrue(ctx.check_hostname)

    def test_opt_out_skip(self):
        with mock.patch.dict(os.environ, {"KB_VERIFY_SSL": "skip"}):
            ctx = fetch_bird._make_ssl_context()
        self.assertFalse(ctx.check_hostname)


if __name__ == "__main__":
    unittest.main()
