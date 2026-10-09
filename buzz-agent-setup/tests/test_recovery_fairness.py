"""Controller fairness at the production round seam; real processes and SQLite."""
import copy
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_recovery_controller as fixture
from recovery_controller import run_round
from test_agent_recovery import seed_originals
from test_recovery_runtime import prewarm_child


class FairnessTests(unittest.TestCase):
    write = fixture.ControllerTests.write
    write_snapshot = fixture.ControllerTests.write_snapshot

    def setUp(self):
        fixture.ControllerTests.setUp(self)
        self.children = []
        self.pids = {self.config["agents"][0]["unit"]: self.pid}
        self.visits = []
        self.relay.validate_route = self.validate_route

    def tearDown(self):
        for child in self.children:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()
        fixture.ControllerTests.tearDown(self)

    def validate_route(self, channel, root, agent):
        self.visits.append((agent, channel, root))
        return "owner-only"

    def inventory(self, agents, threads):
        template = copy.deepcopy(self.config["agents"][0])
        self.config["agents"] = []
        for index in range(agents):
            key = f"{index + 2:064x}"
            pubkey = fixture.wire._signer_pubkey(key)
            name = f"fair-{index}"
            runtime = self.base / name
            runtime.mkdir(mode=0o700)
            child = prewarm_child()
            self.children.append(child)
            current = copy.deepcopy(self.current)
            birth = Path(f"/proc/{child.pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
            current.update(agent_pubkey=pubkey, pid=child.pid, process_start_ticks=birth)
            old = copy.deepcopy(current)
            old.update(generation=str(uuid.UUID(int=98)), boot_id=str(uuid.UUID(int=1)),
                       active={str(uuid.UUID(int=n + 1)): [dict(channel=fixture.CHANNELS[n % 2], root=f"{n + 1:064x}")]
                               for n in range(threads)})
            seed_originals(old)
            for values in old["input_sources"].values():
                values[0]["signed_author"] = fixture.OWNER
            for snap in (current, old):
                self.write(runtime / (snap["generation"] + ".json"), fixture.json.dumps(snap))
            env_file = self.base / (name + ".env")
            self.write(env_file, self.agent_env.read_text()
                       .replace(fixture.AGENT_KEY, key).replace(str(self.runtime), str(runtime)))
            agent = dict(template, name=name, pubkey=pubkey, unit=f"buzz-{name}.service",
                         env_file=str(env_file), journal_dir=str(runtime))
            self.config["agents"].append(agent)
            self.pids[agent["unit"]] = child.pid

    def tick(self):
        self.visits.clear()
        return run_round(self.config, main_pid=self.pids.__getitem__, transport=self.relay)

    def test_each_agent_gets_at_most_two_routes_and_waiting_work_is_visible(self):
        self.inventory(1, 6)
        result = self.tick()
        self.assertEqual(len(self.visits), 2, "one Agent monopolized the round")
        row = result["agents"]["fair-0"]
        self.assertEqual(row["continued"], 2)
        self.assertEqual(row["scheduled"], 2)
        self.assertEqual(row["deferred"], 4)
        self.assertEqual(row["errors"], [])

    def test_global_bound_survives_reopen_and_reordered_inventory_without_starvation(self):
        self.inventory(6, 4)
        seen = set()
        for tick in range(3):
            if tick:
                # The public entry point reopens SQLite every tick. Config
                # ordering must not let the first four Agents monopolize it.
                self.config["agents"].reverse()
            result = self.tick()
            self.assertEqual(len(self.visits), 8, "global route budget is not eight")
            self.assertTrue(all(sum(v[0] == a["pubkey"] for v in self.visits) <= 2
                                for a in self.config["agents"]))
            self.assertFalse(seen.intersection(self.visits), "unvisited pending work starved behind a retry")
            seen.update(self.visits)
            self.assertEqual(sum(row["continued"] for row in result["agents"].values()), 8)
        self.assertEqual(len(seen), 24)
        self.tick()
        self.assertEqual(len(self.relay.events), 48, "revisit signed duplicate continue or startup notices")

    def test_failure_does_not_prevent_next_routes_on_later_rounds(self):
        self.inventory(1, 6)
        def unavailable(channel, root, agent):
            self.visits.append((agent, channel, root))
            raise TimeoutError("sensitive endpoint must not appear in result")
        self.relay.validate_route = unavailable
        visited = set()
        for _ in range(3):
            result = self.tick()
            self.assertEqual(len(self.visits), 2)
            self.assertFalse(visited.intersection(self.visits))
            visited.update(self.visits)
            self.assertEqual(result["agents"]["fair-0"]["continued"], 0)
            self.assertEqual(result["agents"]["fair-0"]["errors"],
                             ["recovery_delivery_unverified"] * 2)
        self.assertEqual(len(visited), 6)

    def test_no_pending_work_preserves_budget_for_later_agents(self):
        self.inventory(6, 0)
        last = self.config["agents"][-1]
        old_path = Path(last["journal_dir"]) / (str(uuid.UUID(int=98)) + ".json")
        old = fixture.json.loads(old_path.read_text())
        old["active"] = {str(uuid.UUID(int=1)): [dict(channel=fixture.CHANNELS[0], root="1" * 64)]}
        seed_originals(old)
        for values in old["input_sources"].values():
            values[0]["signed_author"] = fixture.OWNER
        self.write(old_path, fixture.json.dumps(old))
        result = self.tick()
        self.assertEqual(len(self.visits), 1)
        self.assertEqual(result["agents"][last["name"]]["continued"], 1)

    def test_agent_named_scheduler_cannot_collide_with_round_lock_or_database(self):
        self.inventory(1, 4)
        self.config["agents"][0]["name"] = "scheduler"
        result = self.tick()
        self.assertEqual(result["agents"]["scheduler"]["continued"], 2)
        self.assertEqual(result["agents"]["scheduler"]["errors"], [])


if __name__ == "__main__":
    unittest.main()
