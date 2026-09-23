import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gitlab_maintainer_roster as roster  # noqa: E402

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
ALICE, BOB, CAROL, DAVE = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
PEOPLE = {"alice": ALICE, "bob": BOB, "carol": CAROL}
BASE = "https://gitlab.example"


def member(username, level, **extra):
    return {"username": username, "access_level": level, "state": "active", **extra}


class FakeResponse:
    def __init__(self, body, status=200, next_page=""):
        self.status, self._body, self.headers = status, json.dumps(body).encode(), {"x-next-page": next_page}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def pager(pages):
    """An opener answering successive pages; the next page number is advertised until the last one."""
    calls = iter(range(len(pages)))

    def opener(request, timeout):
        index = next(calls)
        return FakeResponse(pages[index], next_page=str(index + 2) if index + 1 < len(pages) else "")

    return opener


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.people_file = self.write_json("people.json", PEOPLE)
        self.roster_path = self.tmp / "roster.json"

    def write_json(self, name, value, mode=0o600):
        path = self.tmp / name
        path.write_text(json.dumps(value))
        path.chmod(mode)
        return path

    def run_main(self, argv, opener=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        kwargs = {"now": lambda: NOW}
        if opener:
            kwargs["opener"] = opener
        with mock.patch.dict(os.environ, {"GITLAB_TOKEN": "t"} | (env or {})), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = roster.main(argv, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def refresh(self, members):
        return self.run_main(
            ["refresh", "--project-id", "1021", "--base-url", BASE, "--people-file", str(self.people_file),
             "--out", str(self.roster_path)], opener=pager([members]))

    def admit(self, pubkey, *extra):
        return self.run_main(["admit", "--project-id", "1021", "--roster", str(self.roster_path),
                              "--pubkey", pubkey, *extra])


class BuildRosterTests(unittest.TestCase):
    def build(self, members, people=PEOPLE):
        return roster.build_roster(members, people, 1021, NOW)

    def test_keeps_only_active_human_maintainers_with_a_pubkey(self):
        built = self.build([
            member("alice", 40), member("bob", 50), member("carol", 30),          # carol: Developer
            member("dave", 40),                                                     # dave: no pubkey
            member("erin", 40, state="blocked"),
            member("project_1021_bot_ab12", 40), member("robot", 40, bot=True),
        ], {**PEOPLE, "erin": DAVE, "robot": DAVE})
        self.assertEqual([m["username"] for m in built["maintainers"]], ["alice", "bob"])
        self.assertEqual(built["project_id"], 1021)

    def test_person_with_bot_like_prefix_is_not_a_bot(self):
        built = self.build([member("project_1312_botany", 40)], {"project_1312_botany": ALICE})
        self.assertEqual(len(built["maintainers"]), 1)

    def test_one_pubkey_for_two_maintainers_is_refused(self):
        with self.assertRaises(roster.InputError):
            self.build([member("alice", 40), member("bob", 40)], {"alice": ALICE, "bob": ALICE})

    def test_malformed_member_row_fails_closed(self):
        for row in ({"access_level": 40}, {"username": "alice"}, {"username": "alice", "access_level": "40"},
                    {"username": "alice", "access_level": True}):
            with self.assertRaises(roster.RosterError, msg=row):
                self.build([row])


class FetchTests(unittest.TestCase):
    def test_walks_every_page(self):
        rows = roster.fetch_members(BASE, "t", 1021, pager([[member("alice", 40)], [member("bob", 40)]]))
        self.assertEqual([r["username"] for r in rows], ["alice", "bob"])

    def test_http_error_status_and_non_list_body_are_unavailable(self):
        def http_error(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)

        for opener in (http_error, lambda r, timeout: FakeResponse({}, status=200),
                       lambda r, timeout: FakeResponse([], status=403)):
            with self.assertRaises(roster.RosterError):
                roster.fetch_members(BASE, "t", 1021, opener)

    def test_page_walk_that_never_ends_is_incomplete(self):
        with self.assertRaises(roster.RosterError) as caught:
            roster.fetch_members(BASE, "t", 1021, lambda r, timeout: FakeResponse([member("a", 40)], next_page="2"))
        self.assertEqual(caught.exception.reason, "members_api_incomplete")

    def test_malformed_next_page_is_refused(self):
        with self.assertRaises(roster.RosterError):
            roster.fetch_members(BASE, "t", 1021, lambda r, timeout: FakeResponse([member("a", 40)], next_page="x"))

    def test_base_url_must_be_plain_https_origin(self):
        for url in ("http://gitlab.example", "https://gitlab.example/x", "https://u:p@gitlab.example",
                    "https://gitlab.example/"):
            with self.assertRaises(roster.InputError, msg=url):
                roster.fetch_members(url, "t", 1021, pager([[]]))


class AdmitTests(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.refresh([member("alice", 40), member("bob", 30), member("carol", 50)])[0], 0)

    def test_maintainer_is_admitted_and_reason_carries_no_list(self):
        code, out, _ = self.admit(ALICE)
        self.assertEqual((code, json.loads(out)["username"]), (0, "alice"))
        self.assertNotIn(CAROL, out)

    def test_developer_unknown_and_demoted_are_denied(self):
        for pubkey in (BOB, DAVE):
            code, out, _ = self.admit(pubkey)
            self.assertEqual((code, json.loads(out)["reason"]), (1, "not_maintainer"))
        self.refresh([member("alice", 30), member("carol", 50)])   # alice demoted to Developer
        self.assertEqual(self.admit(ALICE)[0], 1)

    def test_display_name_and_case_variants_are_not_pubkeys(self):
        for spoof in ("alice", "@alice", ALICE.upper(), ALICE[:-1], ALICE + "0", ""):
            code, out, _ = self.admit(spoof)
            self.assertEqual((code, json.loads(out)["reason"]), (1, "malformed_pubkey"), spoof)

    def test_missing_roster_is_unavailable_not_denied(self):
        self.roster_path.unlink()
        code, out, _ = self.admit(ALICE)
        self.assertEqual((code, json.loads(out)["reason"]), (2, "roster_unreadable"))

    def test_stale_roster_is_unavailable_and_ttl_is_configurable(self):
        stale = json.loads(self.roster_path.read_text())
        stale["generated_at"] = (NOW - timedelta(minutes=31)).isoformat()
        self.write_json("roster.json", stale)
        code, out, _ = self.admit(ALICE)
        self.assertEqual((code, json.loads(out)["reason"]), (2, "roster_stale"))
        self.assertEqual(self.admit(ALICE, "--ttl", "3600")[0], 0)

    def test_roster_from_the_future_is_unavailable(self):
        future = json.loads(self.roster_path.read_text())
        future["generated_at"] = (NOW + timedelta(hours=1)).isoformat()
        self.write_json("roster.json", future)
        self.assertEqual(json.loads(self.admit(ALICE)[1])["reason"], "roster_stale")

    def test_other_projects_roster_is_unavailable(self):
        code, out, _ = self.run_main(["admit", "--project-id", "1022", "--roster", str(self.roster_path),
                                      "--pubkey", ALICE])
        self.assertEqual((code, json.loads(out)["reason"]), (2, "roster_project_mismatch"))

    def test_malformed_roster_variants_are_unavailable(self):
        good = json.loads(self.roster_path.read_text())
        variants = [
            {**good, "version": 2}, {**good, "maintainers": "x"}, {**good, "generated_at": "yesterday"},
            {**good, "generated_at": "2026-09-20T12:00:00"},   # naive timestamp
            {**good, "project_id": True}, [], {"version": 1},
            {**good, "maintainers": [{"username": "alice", "pubkey": ALICE}, {"username": "bob", "pubkey": ALICE}]},
            {**good, "maintainers": [{"username": "alice", "pubkey": "nothex"}]},
            {**good, "maintainers": ["alice"]},
        ]
        for variant in variants:
            self.write_json("roster.json", variant)
            code, out, _ = self.admit(ALICE)
            self.assertEqual(code, 2, variant)
            self.assertTrue(json.loads(out)["unavailable"])

    def test_insecure_or_symlinked_roster_is_unavailable(self):
        self.roster_path.chmod(0o644)
        self.assertEqual(self.admit(ALICE)[0], 2)
        self.roster_path.chmod(0o600)
        link = self.tmp / "link.json"
        link.symlink_to(self.roster_path)
        code, _, _ = self.run_main(["admit", "--project-id", "1021", "--roster", str(link), "--pubkey", ALICE])
        self.assertEqual(code, 2)

    def test_live_check_sees_a_demotion_the_roster_file_still_lists(self):
        argv = ["admit", "--project-id", "1021", "--live", "--base-url", BASE,
                "--people-file", str(self.people_file), "--pubkey", ALICE]
        self.assertEqual(self.run_main(argv, opener=pager([[member("alice", 40)]]))[0], 0)
        self.assertEqual(self.admit(ALICE)[0], 0)      # the file is still the old answer
        self.assertEqual(self.run_main(argv, opener=pager([[member("alice", 30)]]))[0], 1)

    def test_live_api_failure_is_unavailable_not_denied(self):
        argv = ["admit", "--project-id", "1021", "--live", "--base-url", BASE,
                "--people-file", str(self.people_file), "--pubkey", ALICE]
        code, out, _ = self.run_main(argv, opener=lambda r, timeout: FakeResponse([], status=500))
        self.assertEqual((code, json.loads(out)["reason"]), (2, "members_api_status"))
        self.assertEqual(self.run_main(argv, env={"GITLAB_TOKEN": ""})[0], 2)


class RefreshTests(Base):
    def test_writes_0600_roster_and_prints_only_a_count(self):
        code, out, _ = self.refresh([member("alice", 40)])
        self.assertEqual((code, json.loads(out)), (0, {"written": True, "maintainers": 1}))
        self.assertEqual(stat.S_IMODE(self.roster_path.stat().st_mode), 0o600)
        self.assertNotIn(ALICE, out)

    def test_failed_refresh_leaves_the_previous_roster_untouched(self):
        self.refresh([member("alice", 40)])
        before = self.roster_path.read_text()
        code, _, _ = self.run_main(
            ["refresh", "--project-id", "1021", "--base-url", BASE, "--people-file", str(self.people_file),
             "--out", str(self.roster_path)], opener=lambda r, timeout: FakeResponse([], status=502))
        self.assertEqual(code, 2)
        self.assertEqual(self.roster_path.read_text(), before)

    def test_ambiguous_mapping_writes_nothing(self):
        self.people_file = self.write_json("people.json", {"alice": ALICE, "bob": ALICE})
        code, _, err = self.refresh([member("alice", 40), member("bob", 40)])
        self.assertEqual(code, 1)
        self.assertFalse(self.roster_path.exists())
        self.assertNotIn(ALICE, err)

    def test_people_file_must_be_owner_only_and_well_formed(self):
        self.people_file.chmod(0o644)
        self.assertEqual(self.refresh([member("alice", 40)])[0], 2)
        self.write_json("people.json", {"alice": ALICE.upper()})
        self.assertEqual(self.refresh([member("alice", 40)])[0], 1)
        self.assertFalse(self.roster_path.exists())

    def test_relative_out_and_missing_source_flags_are_refused(self):
        code, _, _ = self.run_main(["refresh", "--project-id", "1021", "--base-url", BASE,
                                    "--people-file", str(self.people_file), "--out", "roster.json"],
                                   opener=pager([[]]))
        self.assertEqual(code, 1)
        self.assertEqual(self.run_main(["refresh", "--project-id", "1021", "--out", str(self.roster_path)])[0], 1)


class ClaimTests(Base):
    def setUp(self):
        super().setUp()
        self.ledger = self.tmp / "ledger"
        self.ledger.mkdir(mode=0o700)

    def claim(self, event_id, ledger=None):
        return self.run_main(["claim", "--ledger-dir", str(ledger or self.ledger), "--event-id", event_id])

    def test_first_claim_wins_and_replay_is_refused(self):
        event = "e" * 64
        self.assertEqual(json.loads(self.claim(event)[1]), {"claimed": True, "reason": "first_claim"})
        code, out, _ = self.claim(event)
        self.assertEqual((code, json.loads(out)["reason"]), (1, "replay"))
        self.assertEqual(self.claim("f" * 64)[0], 0)

    def test_bad_event_id_and_unsafe_ledger_are_refused(self):
        self.assertEqual(self.claim("../x")[0], 1)
        self.assertEqual(self.claim("E" * 64)[0], 1)
        loose = self.tmp / "loose"
        loose.mkdir(mode=0o755)
        self.assertEqual(self.claim("e" * 64, loose)[0], 2)
        self.assertEqual(self.claim("e" * 64, self.tmp / "missing")[0], 2)
        link = self.tmp / "link"
        link.symlink_to(self.ledger)
        self.assertEqual(self.claim("e" * 64, link)[0], 2)


if __name__ == "__main__":
    unittest.main()
