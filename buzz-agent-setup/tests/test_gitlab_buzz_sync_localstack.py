import hashlib
import importlib.util
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


STACK_SCRIPT = Path(__file__).resolve().parent / "localstack" / "stack.py"
E2E_SUPPORT = Path(__file__).resolve().parent / "integration" / "e2e_support.py"


def load_stack_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_localstack", STACK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STACK = load_stack_module()


def load_e2e_support():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_e2e_support", E2E_SUPPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SUPPORT = load_e2e_support()


class AgentRuntimeTest(unittest.TestCase):
    def test_every_localstack_image_is_digest_pinned_and_unknown_tags_fail_closed(self):
        """L1-GIS-092 Relay, data stores and GitLab cannot execute an unregistered mutable image tag."""
        self.assertEqual(STACK.DEFAULT_RELAY_IMAGE, "ghcr.io/block/buzz:0.2.1")
        for image in (
            STACK.DEFAULT_RELAY_IMAGE,
            "ghcr.io/block/buzz:0.2.1",
            STACK.PG_IMAGE,
            STACK.REDIS_IMAGE,
            "gitlab/gitlab-ce:18.0.0-ce.0",
        ):
            self.assertRegex(STACK.PINNED_DIGESTS.get(image, ""), r"^sha256:[0-9a-f]{64}$")

        with mock.patch.object(STACK, "docker") as docker:
            with self.assertRaisesRegex(STACK.StackError, "not pinned"):
                STACK.ensure_image("ghcr.io/block/buzz:unreviewed")
        docker.assert_not_called()

    def test_claude_remains_the_default_runtime(self):
        """L1-GIS-053 Claude remains the default while the E2E harness can select an official ACP adapter."""
        runtime = STACK.resolve_agent_runtime("claude", None)

        self.assertEqual(runtime["command"].name, "claude-agent-acp")
        self.assertEqual(runtime["model"], "opus[1m]")
        self.assertEqual(runtime["extra_env"], {})

    def test_codex_uses_its_official_adapter_without_forcing_a_model(self):
        """L1-GIS-053 Codex uses codex-acp, its configured model and an explicit noninteractive agent mode."""
        runtime = STACK.resolve_agent_runtime("codex", None)

        self.assertEqual(runtime["command"].name, "codex-acp")
        self.assertIsNone(runtime["model"])
        self.assertEqual(runtime["extra_env"]["INITIAL_AGENT_MODE"], "agent-full-access")
        self.assertEqual(runtime["extra_env"]["NO_BROWSER"], "1")
        self.assertEqual(Path(runtime["extra_env"]["CODEX_PATH"]).name, "codex")

    def test_explicit_model_is_preserved_for_either_adapter(self):
        """L1-GIS-053 An explicit model override reaches either official adapter unchanged."""
        self.assertEqual(STACK.resolve_agent_runtime("claude", "sonnet")["model"], "sonnet")
        self.assertEqual(STACK.resolve_agent_runtime("codex", "gpt-test")["model"], "gpt-test")

    def test_up_and_restart_accept_the_same_runtime_switches(self):
        """L1-GIS-053 Initial start and prompt restart expose the same adapter/model contract."""
        parser = STACK.build_parser()

        up = parser.parse_args(["up", "--with-desk", "--agent-adapter", "codex", "--agent-model", "gpt-test"])
        restart = parser.parse_args(
            ["agents", "--restart", "desk,role", "--agent-adapter", "codex", "--agent-model", "gpt-test"]
        )

        for args in (up, restart):
            self.assertEqual(args.agent_adapter, "codex")
            self.assertEqual(args.agent_model, "gpt-test")

    def test_restart_without_runtime_flags_preserves_each_running_adapter(self):
        """L1-GIS-068 Changing respond_to cannot silently replace a running Codex/Claude adapter or its model."""
        parser = STACK.build_parser()
        args = parser.parse_args(["agents", "--restart", "role", "--role-respond-to", "owner-only"])
        self.assertIsNone(args.agent_adapter)
        self.assertIsNone(args.agent_model)

        codex = STACK.restart_agent_runtime({"adapter": "codex", "model": "adapter-default"}, None, None)
        claude = STACK.restart_agent_runtime({"adapter": "claude", "model": "sonnet"}, None, None)
        fresh = STACK.restart_agent_runtime(None, None, None)
        self.assertEqual((codex["adapter"], codex["model"]), ("codex", None))
        self.assertEqual((claude["adapter"], claude["model"]), ("claude", "sonnet"))
        self.assertEqual((fresh["adapter"], fresh["model"]), ("claude", "opus[1m]"))

        override = STACK.restart_agent_runtime({"adapter": "claude", "model": "opus[1m]"}, "codex", "gpt-test")
        self.assertEqual((override["adapter"], override["model"]), ("codex", "gpt-test"))

    def test_failed_agent_start_persists_and_reaps_the_new_process(self):
        """L1-GIS-090 A connection timeout cannot leave an untracked buzz-acp process behind."""
        class RunningProcess:
            pid = 4242
            returncode = None

            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "claude-agent-acp"
            executable.write_bytes(b"test executable")
            executable.chmod(0o700)
            state = {
                "relay": {
                    "http_url": "http://127.0.0.1:18080",
                    "ws_url": "ws://127.0.0.1:18080",
                    "channel_id": "11111111-2222-3333-4444-555555555555",
                },
                "agents": {},
            }
            ids = {
                "owner": {"pubkey": "a" * 64},
                "role": {"secret": "test-role-secret", "auth_tag": "test-role-attestation"},
            }
            runtime = {"command": executable, "adapter": "claude", "model": "test-model", "extra_env": {}}

            with (
                mock.patch.object(STACK, "STATE_DIR", root / "state"),
                mock.patch.object(STACK, "LOGS_DIR", root / "logs"),
                mock.patch.object(STACK, "SECRETS_DIR", root / "secrets"),
                mock.patch.object(STACK, "BUZZ_ACP", executable),
                mock.patch.object(STACK, "require_elf"),
                mock.patch.object(STACK, "node_bin_dir", return_value=root),
                mock.patch.object(STACK.subprocess, "Popen", return_value=RunningProcess()),
                mock.patch.object(STACK, "proc_start_ticks", return_value=99),
                mock.patch.object(STACK, "wait_for", side_effect=STACK.StackError("not connected")),
                mock.patch.object(STACK, "stop_agent", return_value="stopped") as stop,
                mock.patch.object(STACK, "save_state") as save,
            ):
                with self.assertRaisesRegex(STACK.StackError, "not connected"):
                    STACK.start_agent("role", state, ids, runtime)

        failed = state["agents"]["role"]
        self.assertEqual((failed["pid"], failed["status"]), (4242, "start-failed-stopped"))
        stop.assert_called_once_with(failed)
        self.assertGreaterEqual(save.call_count, 1)


class RouteIdentityContractTest(unittest.TestCase):
    def test_content_addressed_desk_release_contains_both_entrypoints_and_dependencies(self):
        """L1-GIS-153 The immutable release holds the timer entrypoint and its runner/publisher/sync/route closure."""
        source = inspect.getsource(STACK.prepare_desk_release)
        for required in (
            "gitlab_buzz_sync_timer.py",
            "gitlab_buzz_desk_runner.py",
            "gitlab_buzz_summary_publish.py",
            "gitlab_buzz_sync.py",
            "gitlab_buzz_route_reply.py",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

        release = "/owner/releases/" + "a" * 64
        rendered = STACK.reference_desk_block(release)
        self.assertNotRegex(rendered, r"(?m)^\s*(?:/usr/bin/)?python3\s")
        self.assertIn("gitlab_buzz_sync_timer.py", rendered)

    def test_http_fallback_retains_a_dedicated_non_agent_identity(self):
        """L1-GIS-066 The non-default HTTP adapter remains isolated and cannot be confused with Desk routing."""
        self.assertEqual(STACK.IDENTITY_NAMES, ("owner", "relay", "desk", "role", "route"))
        self.assertEqual(STACK.ATTESTED_IDENTITIES, ("desk", "role", "route"))
        self.assertNotIn("route", STACK.AGENT_IDENTITIES)

    def test_desk_prompt_runs_no_sync_step_while_desk_keeps_its_own_credentials(self):
        """L1-GIS-073 ADR-0008: the ordinary Desk keeps its GitLab credential but its prompt runs no sync step."""
        self.assertFalse((STACK.SKILL_DIR / "scripts" / "gitlab_buzz_sync_service.py").exists())
        self.assertIn("gitlab_token", inspect.signature(STACK.start_agent).parameters)
        prompt = STACK.reference_desk_block()
        for forbidden in (
            "gitlab_buzz_desk_runner.py",
            "gitlab_buzz_summary_publish.py --summary",
            "gitlab_buzz_sync.py --config",
            "gitlab_buzz_route_reply.py --config",
            "gitlab_buzz_sync_trigger.py",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, prompt)

    def test_localstack_runs_the_timer_entrypoint_with_the_whitelisted_desk_env(self):
        """L1-GIS-192 L2-3 drives sync through `stack.py timer-run`: env whitelist, fixed argv, no restricted Desk."""
        parser = STACK.build_parser()
        self.assertEqual(parser.parse_args(["timer-run"]).func, STACK.cmd_timer_run)
        for retired in (["up", "--desk-heartbeat", "300"], ["agents", "--restart", "desk", "--desk-heartbeat", "300"],
                        ["up", "--desk-restricted-runtime", "/x"]):
            with self.subTest(retired=retired), mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    parser.parse_args(retired)
        self.assertFalse(hasattr(STACK, "restricted_desk_env"))
        self.assertNotIn("restricted_env", inspect.signature(STACK.start_agent).parameters)

        release = Path("/owner/releases/") / ("a" * 64)
        self.assertEqual(STACK.timer_argv(release),
                         ["/usr/bin/python3", str(release / "scripts" / "gitlab_buzz_sync_timer.py")])
        state = {"relay": {"http_url": "http://127.0.0.1:18080"}}
        desk = {"secret": "test-desk-secret", "auth_tag": "test-desk-attestation"}
        env = STACK.timer_env(state, desk, "test-desk-pat")
        self.assertEqual(set(env), {
            "HOME", "USER", "LOGNAME", "PATH", "LANG", "BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY", "BUZZ_AUTH_TAG",
            "BUZZ_DESK_RUNNER_MANIFEST", STACK.DESK_GITLAB_TOKEN_ENV,
        })
        self.assertEqual(env["BUZZ_DESK_RUNNER_MANIFEST"], str(STACK.DESK_RUNNER_MANIFEST_FILE))
        self.assertEqual(env[STACK.DESK_GITLAB_TOKEN_ENV], "test-desk-pat")
        self.assertFalse(any(key.startswith("BUZZ_ACP_") for key in env))

    def test_localstack_desk_env_selects_one_owner_fixed_runner_manifest(self):
        """L1-GIS-135 The timer env (not the Desk Agent env) supplies the owner manifest out of band."""
        release = Path("/owner/releases/") / ("a" * 64)
        manifest = STACK.desk_runner_manifest(release)

        self.assertEqual(manifest, {
            "version": 1,
            "release_dir": str(release),
            "sync": [{
                "config": str(STACK.SYNC_CONFIG_FILE),
                "state_dir": str(STACK.DESK_SYNC_STATE_DIR),
            }],
            "route": {
                "config": str(STACK.ROUTE_CONFIG_FILE),
                "state_dir": str(STACK.DESK_ROUTE_STATE_DIR),
            },
        })
        self.assertNotIn("BUZZ_DESK_RUNNER_MANIFEST", inspect.getsource(STACK.start_agent))
        run_source = inspect.getsource(STACK.cmd_timer_run)
        self.assertIn("Desk runner manifest must be an owner-only regular file", run_source)

    def test_default_route_config_uses_desk_and_code_owned_trust_anchors(self):
        """L1-GIS-120 The default sender is Desk; Canvas cannot choose admins or Agent identities."""
        state = {
            "relay": {"channel_id": "11111111-2222-3333-4444-555555555555"},
            "identities": {
                "owner": {"pubkey": "a" * 64},
                "desk": {"pubkey": "1" * 64},
                "role": {"pubkey": "3" * 64},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz"
            cli.write_bytes(b"test-only buzz cli")
            with mock.patch.object(STACK, "BUZZ_CLI", cli):
                config = STACK.desk_route_config(state, "2026-09-15T00:00:00Z")

        self.assertEqual(config["sender_pubkey"], "1" * 64)
        channel = config["channels"]["11111111-2222-3333-4444-555555555555"]
        self.assertEqual(channel["publisher_pubkey"], "1" * 64)
        self.assertEqual(channel["canvas_admin_pubkeys"], ["a" * 64])
        self.assertEqual(channel["roles"]["role"]["mention_pubkey"], "3" * 64)
        self.assertNotIn("routes", channel)

    def test_sync_config_names_desk_as_publisher(self):
        """L1-GIS-074 Protocol facts are signed by Desk and use its runtime token env."""
        state = {
            "gitlab": {"project_id": 481, "bot": {"user_id": 7}},
            "relay": {"channel_id": "11111111-2222-3333-4444-555555555555",
                      "member_pubkeys": ["1" * 64]},
            "identities": {"desk": {"pubkey": "1" * 64}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "buzz"
            cli_bytes = b"test-only buzz cli"
            cli.write_bytes(cli_bytes)
            with mock.patch.object(STACK, "BUZZ_CLI", cli):
                config = STACK.desk_sync_config(state, "2026-09-13T00:00:00Z")

        self.assertEqual(config["publisher_pubkey"], "1" * 64)
        self.assertNotIn("audience", config)  # ADR-0006: retired, rejected if present
        self.assertEqual(config["gitlab"]["token_env"], "GITLAB_TOKEN")
        self.assertNotIn("desk_pubkey", config)
        self.assertEqual(config["buzz"]["cli_path"], str(cli))
        self.assertEqual(config["buzz"]["cli_sha256"], hashlib.sha256(cli_bytes).hexdigest())

    def test_role_prompt_never_interpolates_untrusted_message_text_into_a_shell_command(self):
        """L1-GIS-071 A Role acknowledgement is constant; source text never becomes shell syntax."""
        prompt = STACK.render_prompt("role", {"relay": {"ws_url": "ws://127.0.0.1:1", "channel_id": "c"}})
        self.assertIn("role-ack: routed", prompt)
        self.assertNotIn("the first line of the message", prompt)
        self.assertIn("Never interpolate", prompt)


class FixtureCredentialLifecycleTest(unittest.TestCase):
    def test_reminted_bot_pat_restarts_desk_runtime(self):
        """L1-GIS-087 A fixture-reminted bot PAT replaces the running Desk credential."""
        old_desk = {"pid": 101, "status": "running", "adapter": "claude", "model": "sonnet"}
        new_desk = {"pid": 202, "status": "running"}
        identities = {
            "owner": {"pubkey": "a" * 64, "secret": "owner", "auth_tag": ""},
            "desk": {"pubkey": "d" * 64, "secret": "desk", "auth_tag": "test-attestation"},
        }
        state = {
            "status": "up",
            "gitlab": {
                "project_id": 481,
                "default_branch": "main",
                "bot": {"username": "bot", "valid": False},
                "outsider": {"username": "outsider", "valid": True},
                "dev": {"username": "dev", "valid": True},
                "maintainer": {"username": "maintainer", "valid": True},
            },
            "agents": {"desk": old_desk},
        }
        fixture = {"revoked_pat_ids": [91], "bot": {"pat": {"token": "test-new-pat"}}}

        with (
            mock.patch.object(STACK, "load_state", return_value=state),
            mock.patch.object(STACK, "private_dir"),
            mock.patch.object(STACK, "gitlab_ready", return_value=True),
            mock.patch.object(STACK, "pat_valid", side_effect=lambda rec: rec["valid"]),
            mock.patch.object(STACK, "gitlab_fixture", return_value=fixture),
            mock.patch.object(STACK, "apply_fixture"),
            mock.patch.object(STACK, "load_identity", side_effect=lambda _state, name: identities[name]),
            mock.patch.object(STACK, "stop_agent", return_value="stopped") as stop,
            mock.patch.object(STACK, "start_agent", return_value=new_desk) as start,
            mock.patch.object(STACK, "save_state"),
        ):
            result, exit_code = STACK.cmd_fixture(None)

        self.assertEqual((exit_code, result["minted"]), (0, ["bot"]))
        stop.assert_called_once_with(old_desk)
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["gitlab_token"], "test-new-pat")
        self.assertIs(state["agents"]["desk"], new_desk)
        self.assertTrue(result["desk_restarted"])


class AgentEvidenceTest(unittest.TestCase):
    def test_codex_tool_outputs_are_scoped_by_workdir_and_start_time(self):
        """L1-GIS-055 Codex E2E evidence reads only this Desk's completed command stdout after the trigger."""
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            matching = sessions / "2026" / "09" / "14" / "matching.jsonl"
            matching.parent.mkdir(parents=True)
            matching.write_text("\n".join(json.dumps(record) for record in [
                {"timestamp": "2026-09-14T02:00:00Z", "type": "session_meta",
                 "payload": {"cwd": "/work/desk"}},
                {"timestamp": "2026-09-14T02:29:59Z", "type": "event_msg",
                 "payload": {"item": {"type": "CommandExecution", "stdout": "before"}}},
                {"timestamp": "2026-09-14T02:30:01Z", "type": "event_msg",
                 "payload": {"item": {"type": "CommandExecution", "stdout": "{\"status\": \"locked\"}\n"}}},
                {"timestamp": "2026-09-14T02:30:02Z", "type": "event_msg",
                 "payload": {"item": {"type": "Reasoning", "stdout": "ignore"}}},
            ]) + "\nnot json\n", encoding="utf-8")

            unrelated = sessions / "2026" / "09" / "14" / "unrelated.jsonl"
            unrelated.write_text("\n".join(json.dumps(record) for record in [
                {"timestamp": "2026-09-14T02:00:00Z", "type": "session_meta",
                 "payload": {"cwd": "/work/other"}},
                {"timestamp": "2026-09-14T02:30:01Z", "type": "event_msg",
                 "payload": {"item": {"type": "CommandExecution", "stdout": "other"}}},
            ]), encoding="utf-8")

            self.assertEqual(
                SUPPORT.codex_tool_outputs(sessions, "/work/desk", "2026-09-14T02:30:00Z"),
                ['{"status": "locked"}\n'],
            )


if __name__ == "__main__":
    unittest.main()
