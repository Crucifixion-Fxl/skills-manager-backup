"""Trace the 28 GitLab Bridge product-demo scenes to L3/L4 evidence.

The product HTML lives in buzz-deploy and is not a dependency of this plugin
repository.  A checked-in manifest is therefore the portable contract.  When
``BUZZ_PRODUCT_DEMO_HTML`` is set, the same test also audits the source HTML so
the two repositories cannot silently drift during a coordinated release.
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import unittest


SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
MANIFEST = SKILL / "tests" / "fixtures" / "gitlab_buzz_product_demo" / "scenarios.json"
SCENARIO_DOC = REPO / "docs" / "testing" / "scenarios" / "tech-gitlab-buzz-bridge.html"
ADR = REPO / "docs" / "05-adr" / "0003-buzz-agent-setup-canvas-desk-routing.md"
DESK_OWNED_ADR = REPO / "docs" / "05-adr" / "0004-run-gitlab-sync-as-desk-owned-agent-step.md"
NATIVE_E2E = SKILL / "tests" / "integration" / "test_routing_e2e.py"
COMPAT_E2E = SKILL / "tests" / "integration" / "test_route_reply_e2e.py"


def load_manifest() -> list[dict]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise AssertionError("product demo manifest must be a JSON array")
    return value


SOURCE_AUDIT_FIELDS = ("id", "title", "status", "dest", "agent", "oracle")


def _top_level_string_fields(source: str) -> dict[str, str]:
    """Read string-valued properties from one flat scene object boundary.

    The product artifact is JavaScript rather than JSON and each scene contains
    nested ``source`` and ``messages`` objects.  A broad regular expression can
    therefore accidentally audit a nested status/destination instead of the
    scene registry.  This deliberately small scanner accepts only top-level
    identifier keys with single-quoted string values and ignores all nested
    values.
    """
    fields: dict[str, str] = {}
    depth = 0
    index = 0
    while index < len(source):
        char = source[index]
        if char in "{[(":
            depth += 1
            index += 1
            continue
        if char in "}])":
            depth -= 1
            index += 1
            continue
        if char in "\"'`":
            quote = char
            index += 1
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                elif source[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            continue
        if depth != 1 or not (char.isalpha() or char in "_$"):
            index += 1
            continue

        key_start = index
        index += 1
        while index < len(source) and (source[index].isalnum() or source[index] in "_$"):
            index += 1
        key = source[key_start:index]
        cursor = index
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor >= len(source) or source[cursor] != ":":
            continue
        cursor += 1
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor >= len(source) or source[cursor] != "'":
            index = cursor
            continue

        cursor += 1
        value: list[str] = []
        while cursor < len(source):
            if source[cursor] == "\\":
                cursor += 1
                if cursor >= len(source):
                    raise AssertionError(f"unterminated escape in scene field {key}")
                value.append({"n": "\n", "r": "\r", "t": "\t"}.get(source[cursor], source[cursor]))
                cursor += 1
            elif source[cursor] == "'":
                fields[key] = "".join(value)
                index = cursor + 1
                break
            else:
                value.append(source[cursor])
                cursor += 1
        else:
            raise AssertionError(f"unterminated string in scene field {key}")
    return fields


def product_scenes(html: str) -> list[dict[str, str]]:
    chunks: list[str] = []
    current: list[str] = []
    scene_start = re.compile(r"^\s*\{\s*id\s*:\s*'\d{2}'")
    for line in html.splitlines():
        if scene_start.match(line):
            if current:
                chunks.append("\n".join(current))
            current = [line]
        elif current and re.match(r"^\s*\];", line):
            chunks.append("\n".join(current))
            current = []
            break
        elif current:
            current.append(line)
    if current:
        chunks.append("\n".join(current))

    scenes: list[dict[str, str]] = []
    for chunk in chunks:
        fields = _top_level_string_fields(chunk)
        missing = set(SOURCE_AUDIT_FIELDS) - fields.keys()
        if missing:
            raise AssertionError(
                f"product scene {fields.get('id', '<unknown>')} missing source-audit fields: "
                f"{sorted(missing)}"
            )
        scenes.append({field: fields[field] for field in SOURCE_AUDIT_FIELDS})
    return scenes


class ProductDemoTraceabilityTest(unittest.TestCase):
    def test_all_27_scenes_have_honest_l3_and_l4_targets(self):
        """L1-GIS-064 Every product demo has L3/L4 IDs, status and a non-empty oracle or blocker."""
        scenes = load_manifest()
        # Scene 14 (withhold Diff when no unique Issue) was retired on 2026-09-17: Diffs follow the
        # deterministic MR→Issue association (L1-GIS-175..177); since ADR-0015 (2026-09-21) they go to the binding
        # thread only.
        self.assertEqual([scene["id"] for scene in scenes], [f"{number:02d}" for number in range(1, 29) if number != 14])
        self.assertEqual(len({scene["title"] for scene in scenes}), 27)
        self.assertEqual(Counter(scene["l3_status"] for scene in scenes),
                         Counter({"automated": 13, "partial": 9, "blocked": 5}))
        self.assertEqual(Counter(scene["l4_status"] for scene in scenes),
                         Counter({"specified": 22, "blocked": 5}))
        for scene in scenes:
            with self.subTest(scene=scene["id"]):
                self.assertRegex(scene["l3_case"], r"^L3-GIS-DEMO-\d{2}$")
                self.assertRegex(scene["l4_case"], r"^L4-GIS-DEMO-\d{2}$")
                self.assertIn(scene["l3_status"], {"automated", "partial", "blocked"})
                self.assertIn(scene["l4_status"], {"specified", "blocked"})
                self.assertTrue(scene.get("destination"))
                self.assertTrue(scene.get("agent"))
                self.assertTrue(scene.get("oracle"))
                if scene["l3_status"] == "partial":
                    self.assertTrue(scene.get("gap"))
                    self.assertNotIn("evidence", scene)
                if scene["l3_status"] == "automated":
                    self.assertNotIn("gap", scene)
                    self.assertNotIn("blocker", scene)
                if "blocked" in {scene["l3_status"], scene["l4_status"]}:
                    self.assertTrue(scene.get("blocker"))
                    self.assertNotIn("evidence", scene)

    def test_diff_scene_follows_the_binding_thread_only(self):
        """L1-GIS-178（ADR-0015 改）Scene 13 describes Diff going to the MR's binding thread only (the other related threads only get a cross-link); scene 14 stays retired."""
        scenes = {scene["id"]: scene for scene in load_manifest()}
        self.assertNotIn("14", scenes)
        scene = scenes["13"]
        self.assertEqual(scene["title"], "Diff 只进 MR 的 binding Thread")
        self.assertEqual(scene["l3_status"], "partial")
        self.assertEqual(scene["l4_status"], "specified")
        self.assertIn("L1-GIS-175", scene["gap"])
        self.assertIn("交叉链接", scene["oracle"])
        self.assertNotIn("每个关联 Issue", json.dumps(scene, ensure_ascii=False))
        self.assertNotIn("L4-GIS-DEMO-14", SCENARIO_DOC.read_text(encoding="utf-8"))

    def test_automated_l3_evidence_and_every_l4_case_are_navigable(self):
        """L1-GIS-064 Automated claims name executable tests; every L4 case is specified in the HTML plan."""
        sources = {
            path.relative_to(REPO).as_posix(): path.read_text(encoding="utf-8")
            for path in (SKILL / "tests" / "integration").glob("test_*.py")
        }
        l4 = SCENARIO_DOC.read_text(encoding="utf-8")
        for count, label in ((13, "当前已有完整自动化 L3"), (9, "只有局部证据，待补 L3"),
                             (5, "被架构或未实现能力阻塞")):
            self.assertRegex(l4, rf"<b>{count}</b>\s*<span>{label}</span>")
        for scene in load_manifest():
            with self.subTest(scene=scene["id"]):
                self.assertIn(scene["l4_case"], l4)
                if scene["l3_status"] == "automated":
                    evidence = scene.get("evidence")
                    self.assertIn(evidence, sources)
                    self.assertIn(scene["l3_case"], sources[evidence])

    def test_routing_adr_records_canvas_desk_default_and_http_downgrade(self):
        """L1-GIS-065 Canvas+Desk is Accepted; Workflow secret exposure remains explicit."""
        text = ADR.read_text(encoding="utf-8")
        for needle in (
            "status: Accepted",
            'date: "2026-09-15"',
            "deciders:",
            "0002-buzz-agent-setup-gitlab-thread-routing.md",
            "superseded-by: []",
            "## Context and Problem Statement",
            "## Considered Options",
            "## Trade-off Analysis",
            "## Decision Outcome",
            "## Consequences",
            "Desk turn",
            "Canvas",
            "BIP-340",
            "--scan-once",
            "X-Route-Secret",
            "workflows get",
            "reply_in_thread",
            "不能和本地 Desk gate 同时启用",
        ):
            self.assertIn(needle, text)

    def test_desk_owned_adr_records_current_bearer_and_mode_lease_boundary(self):
        """L1-GIS-125 Current ownership facts live in ADR-0004, without rewriting Accepted ADR-0003."""
        text = DESK_OWNED_ADR.read_text(encoding="utf-8")
        for needle in (
            "status: Accepted",
            'date: "2026-09-16"',
            "Desk-owned Agent Steps",
            "Channel-member-readable anti-abuse material",
            "shared mode lease",
            "Keep Naturehood HTTP fallback disabled",
        ):
            self.assertIn(needle, text)

    def test_demo_routing_evidence_separates_default_canvas_and_http_fallback(self):
        """L1-GIS-094 Default L3 uses Desk+Canvas on 0.2.1; HTTP compatibility remains explicit."""
        native = NATIVE_E2E.read_text(encoding="utf-8")
        compat = COMPAT_E2E.read_text(encoding="utf-8")
        self.assertIn('COMPAT_RELAY_IMAGE = "ghcr.io/block/buzz:0.2.1"', compat)
        self.assertIn("No route Workflow is created", native)
        self.assertIn("self.set_route_canvas()", native)
        self.assertIn("self.run_route()", native)
        self.assertIn("test_011_untrusted_latest_canvas_fails_closed", native)
        self.assertIn("compatibility routing L3 requires relay image", compat)
        for needle in (
            'self.assert_single_binding("issue", issue["iid"], root)',
            'self.assertEqual(len(self.route_replies(root["id"])), 1)',
            'self.assertEqual(len(self.role_replies(root["id"])), 1)',
            'self.assertEqual([event["id"] for event in self.role_replies(root["id"])], role_ids)',
        ):
            self.assertIn(needle, native)


@unittest.skipUnless(os.environ.get("BUZZ_PRODUCT_DEMO_HTML"),
                     "set BUZZ_PRODUCT_DEMO_HTML to audit the buzz-deploy product source")
class ProductDemoSourceAuditTest(unittest.TestCase):
    def test_source_scene_registry_matches_the_portable_contract(self):
        """L3-GIS-DEMO-CONTRACT-01 The real product artifact exposes exactly the 28 traced scenarios."""
        path = Path(os.environ["BUZZ_PRODUCT_DEMO_HTML"])
        html = path.read_text(encoding="utf-8")
        expected = [
            {
                "id": scene["id"],
                "title": scene["title"],
                "status": scene["l3_status"],
                "dest": scene["destination"],
                "agent": scene["agent"],
                "oracle": scene["oracle"],
            }
            for scene in load_manifest()
        ]
        self.assertEqual(product_scenes(html), expected)

    def test_source_script_has_no_duplicate_block_scoped_declaration(self):
        """L3-GIS-DEMO-CONTRACT-02 The actual interactive Demo can parse instead of failing before scene 01."""
        html = Path(os.environ["BUZZ_PRODUCT_DEMO_HTML"]).read_text(encoding="utf-8")
        bridge_renderer = re.search(r"function bridgeTextFor\(scene\) \{(.*?)\n    \}", html, re.S)
        self.assertIsNotNone(bridge_renderer)
        declarations = re.findall(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=", bridge_renderer.group(1))
        self.assertEqual(len(declarations), len(set(declarations)), declarations)


if __name__ == "__main__":
    unittest.main()
