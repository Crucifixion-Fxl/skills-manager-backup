"""REC-019: bound a COMPLETE original-source read, not each request alone."""
import copy
import json
import signal
import http.server
import threading
import time
import unittest
import urllib.error
from unittest import mock

import test_agent_recovery as journal
import test_recovery_source_authority as signed


class ReadBudgetTests(unittest.TestCase):
    setUp = signed.OriginalAuthorityTests.setUp
    add = signed.OriginalAuthorityTests.add
    http = signed.OriginalAuthorityTests.http
    bind = signed.OriginalAuthorityTests.bind
    source_request = signed.OriginalAuthorityTests.source_request
    roster = signed.OriginalAuthorityTests.roster
    policy_mode = signed.OriginalAuthorityTests.policy_mode
    delivered_work = signed.OriginalAuthorityTests.delivered_work

    def run_recovery(self):
        with signed.RecoveryStore(self.db) as store:
            return signed.recover(store, self.transport, agent_name="test-dev", agent_pubkey=signed.signed.AGENT,
                                  relay=self.current["relay"], current=self.current, snapshots=[self.old], runtime_verified=True)

    def test_total_deadline_never_authorizes_a_partially_read_original_set(self):
        self.roster()
        self.policy_mode("anyone")
        self.source_request()
        original = copy.deepcopy(self.old)
        clock, timeouts = [0.0], []
        def slow(url, headers, timeout, body=None):
            timeouts.append(timeout)
            clock[0] += 3
            return self.http(url, headers, timeout, body)
        self.transport.http = slow
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            result = self.run_recovery()
        self.assertEqual(result["continued"], 0, "per-request timeout allowed incomplete batch verification")
        self.assertIn("source_read_timeout", result["errors"])
        self.assertEqual(self.old, original)
        self.assertEqual(self.delivered_work(), set())
        self.assertTrue(any(value < 10 for value in timeouts), "each request restarted a full ten-second timeout")
        notices = [e for e in self.events if "核验" in e["content"] and e["pubkey"] == signed.signed.OWNER]
        self.assertEqual(len(notices), 1)
        self.assertIn("owner", notices[0]["content"])
        self.assertFalse(any(t[0] == "p" for t in notices[0]["tags"]))
        self.transport.http = self.http
        self.assertEqual(self.run_recovery()["continued"], 1, "timed-out work was lost or permanently suppressed")
        self.assertEqual(self.delivered_work(), set(self.old["input_sources"]))

    def large_sources(self, size):
        for number in range(2):
            event = self.add(signed.signed.OWNER_KEY, 9,
                [["h", signed.signed.CHANNEL], ["e", self.root["id"], "", "root"], ["e", self.root["id"], "", "reply"]],
                str(number) + "PRIVATE ORIGINAL " + "x" * size)
            self.bind(event)

    def test_response_bytes_are_bounded_across_the_complete_read(self):
        self.large_sources(550_000)
        original = copy.deepcopy(self.old)
        result = self.run_recovery()
        self.assertEqual(result["continued"], 0, "individually bounded responses bypassed the aggregate cap")
        self.assertIn("source_read_capacity_exceeded", result["errors"])
        self.assertEqual(self.old, original)
        self.assertEqual(self.delivered_work(), set())
        notices = [e for e in self.events if e["pubkey"] == signed.signed.OWNER and "核验" in e["content"]]
        self.assertEqual(len(notices), 1)
        self.assertNotIn("PRIVATE ORIGINAL", notices[0]["content"])

    def test_valid_complete_read_below_budget_still_resumes_all_originals(self):
        self.large_sources(350_000)
        original_publish = self.transport.publish
        def publish(event):
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0), "read alarm leaked into a write")
            return original_publish(event)
        self.transport.publish = publish
        result = self.run_recovery()
        events = result.pop("continued_events")
        self.assertEqual(result, {"continued": 1, "errors": []})
        self.assertEqual(len(events), 1)
        self.assertEqual(self.delivered_work(), set(self.old["input_sources"]))
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def socket_timeout(self, error):
        request = self.source_request(key=signed.signed.OWNER_KEY)
        def timeout(url, headers, seconds, body=None):
            if url.endswith("/query") and any(q.get("ids") == [request["id"]] for q in json.loads(body)):
                raise error
            return self.http(url, headers, seconds, body)
        self.transport.http = timeout
        result = self.run_recovery()
        self.assertEqual(result["continued"], 0, "socket timeout authorized a partial original set")
        self.assertIn("source_read_timeout", result["errors"])
        self.assertEqual(self.delivered_work(), set())
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_socket_timeout_cannot_be_mistaken_for_one_denied_source(self):
        self.socket_timeout(TimeoutError("private socket"))

    def test_wrapped_socket_timeout_cannot_authorize_a_partial_source_set(self):
        self.socket_timeout(urllib.error.URLError(TimeoutError("private host")))

    def test_blocked_alarm_fails_closed_instead_of_claiming_a_hard_deadline(self):
        original = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        try:
            result = self.run_recovery()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, original)
        self.assertEqual(result["continued"], 0)
        self.assertTrue(result["errors"])
        self.assertEqual(self.delivered_work(), set())
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_one_slow_thread_does_not_prevent_another_thread_recovery(self):
        self.source_request(key=signed.signed.OWNER_KEY)
        bad_root = self.root
        good = self.add(signed.signed.OWNER_KEY, 9, [["h", signed.signed.CHANNEL]], "second Thread")
        self.root = good
        self.bind(good)
        self.root = bad_root
        clock = [0.0]
        bad_ids = set(self.old["input_sources"]) - {good["id"]}
        def slow(url, headers, timeout, body=None):
            if url.endswith("/query") and any(set(q.get("ids", [])).intersection(bad_ids) for q in json.loads(body)):
                clock[0] += 6
            return self.http(url, headers, timeout, body)
        self.transport.http = slow
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            result = self.run_recovery()
        self.assertEqual(result["continued"], 1)
        self.assertIn("source_read_timeout", result["errors"])
        self.assertEqual(self.delivered_work(), {good["id"]})

    def test_real_trickling_http_cannot_keep_source_read_alive_indefinitely(self):
        """Real urllib/socket/clock: bytes arrive below the socket timeout."""
        request = self.source_request(key=signed.signed.OWNER_KEY)
        fixture = self
        stopped, reached = threading.Event(), threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                status, answer = fixture.http("http://local" + self.path, self.headers, 10, body)
                slow = self.path == "/query" and any(q.get("ids") == [request["id"]] for q in json.loads(body))
                self.send_response(status)
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                try:
                    if slow:
                        reached.set()
                        # Sixteen seconds of continuous bytes: each read makes
                        # progress, so an ordinary socket timeout never fires.
                        for part in range(40):
                            self.wfile.write(answer[len(answer) * part // 40:len(answer) * (part + 1) // 40])
                            self.wfile.flush()
                            if stopped.wait(0.4):
                                break
                    else:
                        self.wfile.write(answer)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def cleanup():
            stopped.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
        self.addCleanup(cleanup)
        origin = f"http://127.0.0.1:{server.server_port}"
        self.transport.origin = self.old["relay"] = self.current["relay"] = origin
        self.transport.http = signed.signed.wire._http_get
        started = time.monotonic()
        result = self.run_recovery()
        elapsed = time.monotonic() - started
        stopped.set()
        self.assertTrue(reached.is_set(), "the fault never reached the source HTTP read")
        self.assertLess(elapsed, 13, "progressing bytes reset the timeout and held the controller")
        self.assertEqual(result["continued"], 0)
        self.assertIn("source_read_timeout", result["errors"])
        self.assertEqual(self.delivered_work(), set())


class SourceCountTests(unittest.TestCase):
    setUp = journal.RecoveryTests.setUp
    tearDown = journal.RecoveryTests.tearDown
    run_recovery = journal.RecoveryTests.run_recovery

    def sources(self, count):
        self.old = journal.snapshot(self.old["generation"], {
            f"00000000-0000-0000-0000-{n:012d}": [dict(channel=journal.CHANNELS[0], root="1" * 64)]
            for n in range(1, count + 1)})

    def test_oversized_original_set_is_visible_and_never_truncated_into_success(self):
        self.sources(257)
        original = copy.deepcopy(self.old)
        result = self.run_recovery()
        self.assertEqual(result["continued"], 0)
        self.assertIn("source_read_capacity_exceeded", result["errors"])
        self.assertEqual(self.old, original)
        self.assertFalse(any(e["content"].endswith(" continue") for e in self.relay.events.values()))

    def test_exactly_256_originals_positive_control(self):
        self.sources(256)
        self.assertEqual(self.run_recovery()["continued"], 1)
        event = next(e for e in self.relay.events.values() if e["content"].endswith(" continue"))
        self.assertEqual(len(next(t for t in event["tags"] if t[0] == "recovery")[3:]), 256)


if __name__ == "__main__":
    unittest.main()
