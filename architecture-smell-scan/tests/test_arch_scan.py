"""architecture-smell-scan 脚本的单元测试与端到端测试（不联网；CI 镜像装有 git）。"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import arch_scan as A  # noqa: E402


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def require_git():
    if shutil.which("git"):
        return
    if os.environ.get("CI"):
        pytest.fail("CI 镜像必须安装 git，否则 git 采集路径没有覆盖")
    pytest.skip("git not installed")


def git(repo, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                   check=True, capture_output=True, env=env)


def mk_raw(nloc: dict, edges: list, commits=None):
    """直接构造 raw.json（每个模块一个文件 <模块>/f.go），用于判定规则的边界测试。"""
    files = {f"{m}/f.go": {"lang": "go", "nloc": n, "lines": n, "test": False, "generated": False}
             for m, n in nloc.items()}
    return {"schema_version": 1, "repo": "/nonexistent", "head": None, "since": None, "notes": [],
            "files": files, "edges": [{"src": f"{s}/f.go", "dst": d, "line": 1} for s, d in edges],
            "external_imports": {}, "sql_tables": [], "commits": commits or [], "clones": None,
            "draft_modules": [], "build_units": []}


def mk_map(*names, **extra):
    return {"modules": [{"name": n, "paths": [n]} for n in names], **extra}


def ids_of(res):
    return {(c["id"], tuple(c["modules"])) for c in res["candidates"]}


GO_FILES = {
    "go.mod": "module example.com/app\n\ngo 1.22\n",
    "cmd/server/main.go": 'package main\n\nimport "example.com/app/internal/common/app"\n\nfunc main() { app.Run() }\n',
    "internal/common/app/app.go": (
        "package app\n\nimport (\n\t\"fmt\"\n\tshadow \"example.com/app/internal/shadow/service\"\n)\n\n"
        "func Run() { fmt.Println(shadow.X) }\n"
    ),
    "internal/common/config/config.go": "package config\n\n// Port 端口\nvar Port = 80\n",
    "internal/shadow/service/s.go": (
        'package service\n\nimport "example.com/app/internal/common/config"\n\n'
        'var X = config.Port\nconst q = "SELECT id FROM devices WHERE x"\n'
    ),
    "internal/shadow/service/s_test.go": 'package service\n\nimport "example.com/app/internal/common/app"\n',
    "internal/gateway/g.go": 'package gateway\n\nconst q = "UPDATE devices SET a=1"\n',
}

GO_MAP = {
    "modules": [
        {"name": "server", "paths": ["cmd/server"], "role": "root"},
        {"name": "common", "paths": ["internal/common"], "role": "base"},
        {"name": "shadow", "paths": ["internal/shadow"], "role": "business"},
        {"name": "gateway", "paths": ["internal/gateway"], "role": "business"},
    ]
}


@pytest.fixture
def go_raw(tmp_path):
    make_repo(tmp_path, GO_FILES)
    return A.collect(str(tmp_path), use_git=False, use_jscpd=False)


def edges_of(raw):
    return {(e["src"], e["dst"], e["line"]) for e in raw["edges"]}


# ---------------------------------------------------------------- 解析

def test_go_imports_resolve_to_package_dirs_with_line_numbers(go_raw):
    e = edges_of(go_raw)
    assert ("internal/common/app/app.go", "internal/shadow/service", 5) in e
    assert ("internal/shadow/service/s.go", "internal/common/config", 3) in e
    assert "fmt" in go_raw["external_imports"]


def test_nloc_skips_blank_and_comment_lines_and_records_physical_lines(go_raw):
    meta = go_raw["files"]["internal/common/config/config.go"]
    assert meta["nloc"] == 2 and meta["lines"] == 4


def test_test_files_are_marked_and_ignored_for_module_edges(go_raw):
    assert go_raw["files"]["internal/shadow/service/s_test.go"]["test"] is True
    res = A.analyze(go_raw, GO_MAP)
    ev = [x for x in res["module_edges"] if x["src"] == "shadow"][0]["evidence"]
    assert all("_test.go" not in line for line in ev)


def test_python_absolute_relative_parenthesized_and_parent_package_imports(tmp_path):
    make_repo(tmp_path, {
        "pkg/__init__.py": "helper = 1\n",
        "pkg/a.py": "from .b import f\nimport os\n",
        "pkg/b.py": "def f():\n    pass\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/c.py": "from .. import helper\n",
        "main.py": "import pkg.a\nfrom pkg import b\n",
        "top.py": "from pkg import (\n    a,\n    b,\n)\n",
    })
    e = {(x["src"], x["dst"]) for x in A.collect(str(tmp_path), use_git=False, use_jscpd=False)["edges"]}
    assert {("pkg/a.py", "pkg/b.py"), ("main.py", "pkg/a.py"), ("main.py", "pkg/b.py")} <= e
    assert ("top.py", "pkg/a.py") in e and ("top.py", "pkg/b.py") in e
    assert ("pkg/sub/c.py", "pkg") in e


def test_ts_relative_multiline_alias_nodenext_and_vue(tmp_path):
    make_repo(tmp_path, {
        "tsconfig.json": ('{\n  // 注释\n  "compilerOptions": {"baseUrl": ".", '
                          '"paths": {"@/*": ["src/*"]} /* Specify path mapping */,},\n}\n'),
        "src/a/index.ts": "import { x } from '../b/util';\nimport {\n  y,\n} from '@/c';\nimport React from 'react';\n",
        "src/b/util.ts": "export const x = 1;\n",
        "src/c/index.ts": "export const y = 2;\n",
        "src/n/index.ts": "import { u } from './util.js';\n",
        "src/n/util.ts": "export const u = 1;\n",
        "src/App.vue": "<script>\nimport client from './api/client'\n</script>\n",
        "src/api/client.ts": "export default 1;\n",
    })
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    e = {(x["src"], x["dst"]) for x in raw["edges"]}
    assert ("src/a/index.ts", "src/b/util.ts") in e
    assert ("src/a/index.ts", "src/c/index.ts") in e
    assert ("src/n/index.ts", "src/n/util.ts") in e
    assert ("src/App.vue", "src/api/client.ts") in e
    assert "react" in raw["external_imports"]


def test_tsconfig_with_bad_types_is_reported_not_crashing(tmp_path):
    make_repo(tmp_path, {"tsconfig.json": '{"compilerOptions": {"baseUrl": 1, "paths": []}}', "a.ts": "export {}\n"})
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    assert any("tsconfig" in n for n in raw["notes"])


def test_tsconfig_alias_uses_nearest_config_deterministically(tmp_path):
    cfg = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
    make_repo(tmp_path, {
        "apps/web/tsconfig.json": cfg, "apps/admin/tsconfig.json": cfg,
        "apps/web/src/utils.ts": "export const a = 1;\n",
        "apps/admin/src/utils.ts": "export const b = 1;\n",
        "apps/admin/src/page.ts": "import { b } from '@/utils';\n",
    })
    results = set()
    for seed in ("0", "1", "2"):
        out = tmp_path / f"raw{seed}.json"
        subprocess.run([sys.executable, str(SCRIPTS / "arch_scan.py"), "collect", str(tmp_path), "--out", str(out),
                        "--no-git", "--no-jscpd"], check=True, capture_output=True,
                       env=dict(os.environ, PYTHONHASHSEED=seed))
        raw = json.loads(out.read_text(encoding="utf-8"))
        results.add(tuple(sorted((x["src"], x["dst"]) for x in raw["edges"])))
    assert results == {(("apps/admin/src/page.ts", "apps/admin/src/utils.ts"),)}


def test_java_dart_swift_and_c_resolution(tmp_path):
    make_repo(tmp_path, {
        "src/main/java/com/acme/order/Order.java": "package com.acme.order;\npublic class Order {}\n",
        "src/main/java/com/acme/billing/Bill.java": "package com.acme.billing;\nimport com.acme.order.Order;\nclass Bill {}\n",
        "src/main/java/com/acme/App.java": "package com.acme;\nimport com.acme.sharedlib.money.Money;\nclass App {}\n",
        "app/pubspec.yaml": "name: app\n",
        "app/lib/features/a/a.dart": "import 'package:app/core/c.dart';\nimport 'package:flutter/material.dart';\nimport '../b/b.dart';\n",
        "app/lib/features/b/b.dart": "class B {}\n",
        "app/lib/core/c.dart": "class C {}\n",
        "ios/Sources/Net/Client.swift": "public struct Client {}\n",
        "ios/Sources/Feed/Feed.swift": "import Foundation\nimport Net\n",
        "App/Network/Reach.swift": "struct Reach {}\n",
        "App/Feed/FeedView.swift": "import Network\n",
        "fw/src/hal/gpio.c": '#include "osal/lock.h"\n#include <stdio.h>\n#include "imu_manager.h"\n',
        "fw/src/osal/lock.h": "void lock(void);\n",
        "tools/calib/test_stubs/imu_manager.h": "void imu(void);\n",
    })
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    e = {(x["src"], x["dst"]) for x in raw["edges"]}
    assert ("src/main/java/com/acme/billing/Bill.java", "src/main/java/com/acme/order") in e
    assert not any(s.endswith("App.java") for s, _ in e)          # 外部 jar 不连到根包
    assert ("app/lib/features/a/a.dart", "app/lib/core/c.dart") in e
    assert ("app/lib/features/a/a.dart", "app/lib/features/b/b.dart") in e
    assert ("ios/Sources/Feed/Feed.swift", "ios/Sources/Net") in e
    assert not any(s == "App/Feed/FeedView.swift" for s, _ in e)  # 系统框架 Network 不是内部依赖
    assert ("fw/src/hal/gpio.c", "fw/src/osal/lock.h") in e
    assert not any(d.endswith("imu_manager.h") for _, d in e)     # 只命中测试桩的头文件视为外部
    assert "package:flutter" in raw["external_imports"]


@pytest.mark.parametrize("path,expected", [
    ("apps/soc/tests_gtest/x.cpp", True), ("tools/c/test_stubs/a.h", True), ("ios/AppTests/Helpers.swift", True),
    ("src/foo_test.cc", True), ("src/foo_unittest.cpp", True), ("src/test_foo.c", True), ("src/bar_tests.kt", True),
    ("a/testdata/x.go", True), ("e2e-tests/x.ts", True), ("x_test.go", True), ("src/FooTest.java", True),
    ("api/spec/openapi.go", False), ("pkg/testing/clock.go", False), ("latest/x.go", False), ("src/contest.py", False),
])
def test_is_test_path(path, expected):
    assert A.is_test_path(path) is expected


def test_symlinks_and_oversized_files_are_skipped(tmp_path):
    outside = tmp_path / "outside"
    make_repo(outside, {"secret.go": 'package x\nconst token = "sk-live-FAKE"\n'})
    repo = make_repo(tmp_path / "repo", {"platform/p.go": "package platform\n"})
    os.symlink(outside / "secret.go", repo / "platform" / "leak.go")
    (repo / "big.go").write_text("package big\n" + "// x\n" * 600_000, encoding="utf-8")
    raw = A.collect(str(repo), use_git=False, use_jscpd=False)
    assert "platform/leak.go" not in raw["files"]
    assert "big.go" not in raw["files"]
    assert any("2MB" in n or "big.go" in n for n in raw["notes"])


def test_regexes_on_hostile_lines_stay_linear():
    for rx, text in [(A.JAVA_IMPORT, "import a" + " " * 20000 + "!"),
                     (A.FIX_RE, "fix" + " " * 20000 + "x"),
                     (A.PY_FROM, "from " + "." * 20000 + " x")]:
        t = time.perf_counter()
        rx.match(text)
        assert time.perf_counter() - t < 0.5, rx.pattern


def test_non_utf8_filename_does_not_break_collect_or_dump(tmp_path):
    make_repo(tmp_path, {"ok.go": "package ok\n"})
    fd = os.open(os.path.join(os.fsencode(tmp_path), b"\xb2\xe2.go"), os.O_WRONLY | os.O_CREAT)
    os.write(fd, b"package gbk\n")
    os.close(fd)
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    out = tmp_path / "raw.json"
    A._dump(raw, str(out))
    assert "ok.go" in json.loads(out.read_text(encoding="utf-8"))["files"]


# ---------------------------------------------------------------- 判定规则

def test_go_fixture_exact_candidate_set(go_raw):
    res = A.analyze(go_raw, GO_MAP)
    assert ids_of(res) == {
        ("M06", ("common", "shadow")), ("M04", ("common", "shadow")), ("M09", ("common",)),
        ("M13", ("gateway", "shadow")), ("M14", ("common",)),
    }
    assert all(c["evidence"] for c in res["candidates"])
    assert [c["key"] for c in res["candidates"] if c["id"] == "M13"] == ["devices"]
    assert any("M03" in n for n in res["notes"]) and any("M05" in n for n in res["notes"])


def test_canonical_modules_are_not_reported_as_vague_names(go_raw):
    spec = json.loads(json.dumps(GO_MAP))
    spec["modules"][1]["canonical"] = True
    assert ("M14", ("common",)) not in ids_of(A.analyze(go_raw, spec))


def test_layer_public_interface_and_controlled_libs(tmp_path):
    make_repo(tmp_path, {
        "go.mod": "module ex.com/m\n",
        "feature/a/a.go": 'package a\n\nimport "ex.com/m/sdk/pay/internal/impl"\n',
        "feature/c/c.go": 'package c\n\nimport "ex.com/m/sdk/pay/ports"\n',
        "sdk/pay/ports/p.go": "package ports\n",
        "sdk/pay/internal/impl/i.go": ('package impl\n\nimport (\n\t"ex.com/m/feature/b"\n'
                                       '\t"github.com/stripe/stripe-go/v76"\n\t"github.com/stripe/stripe-go/v76/customer"\n)\n'),
        "feature/b/b.go": "package b\n",
    })
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    spec = {
        "layers": ["feature", "sdk"],
        "modules": [
            {"name": "a", "paths": ["feature/a"], "layer": "feature"},
            {"name": "b", "paths": ["feature/b"], "layer": "feature"},
            {"name": "c", "paths": ["feature/c"], "layer": "feature"},
            {"name": "pay", "paths": ["sdk/pay"], "layer": "sdk", "public": ["sdk/pay/ports"]},
        ],
        "controlled_libs": [{"lib": "github.com/stripe/stripe-go", "only_in": ["billing"]}],
    }
    res = A.analyze(raw, spec)
    ids = ids_of(res)
    assert ("M01", ("b", "pay")) in ids            # sdk 层依赖 feature 层
    assert ("M02", ("a", "pay")) in ids            # 绕过 ports
    assert ("M02", ("c", "pay")) not in ids        # 经 ports 不报
    assert ("M01", ("a", "pay")) not in ids        # 向下依赖不报
    assert [c["id"] for c in res["candidates"]].count("M03") == 1   # 子包合并成一条
    assert res["summary"]["violation_edges"] == 2


def test_java_controlled_lib_matches_dotted_imports(tmp_path):
    make_repo(tmp_path, {"src/main/java/com/acme/billing/Pay.java":
                         "package com.acme.billing;\nimport com.stripe.model.Charge;\nclass Pay {}\n"})
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    spec = {"modules": [{"name": "billing", "paths": ["src/main/java/com/acme/billing"]}],
            "controlled_libs": [{"lib": "com.stripe", "only_in": ["gateway"]}]}
    assert ("M03", ("billing",)) in ids_of(A.analyze(raw, spec))


def test_isolated_allow_exclude_and_leaf():
    raw = mk_raw({"x": 10, "y": 10, "z": 10, "u": 10, "lib": 10, "lib/vendor": 10},
                 [("x", "y/f.go"), ("u", "z/f.go"), ("lib/vendor", "x/f.go")])
    base = {"modules": [{"name": "x", "paths": ["x"]}, {"name": "y", "paths": ["y"]}, {"name": "z", "paths": ["z"]},
                        {"name": "u", "paths": ["u"], "role": "leaf"},
                        {"name": "lib", "paths": ["lib"], "exclude": ["lib/vendor"]}],
            "isolated": [["x", "y"]]}
    res = A.analyze(raw, base)
    ids = ids_of(res)
    assert ("M02", ("x", "y")) in ids
    assert ("M04", ("u", "z")) in ids
    assert "lib/vendor/f.go" in res["unmapped_files"]
    allowed = dict(base, allow=[["x", "y"]])
    assert ("M02", ("x", "y")) not in ids_of(A.analyze(raw, allowed))


def test_violation_edges_count_distinct_module_pairs():
    raw = mk_raw({"d": 10, "u": 10}, [("d", "u/internal/f.go")])
    spec = {"layers": ["up", "down"],
            "modules": [{"name": "u", "paths": ["u"], "layer": "up", "public": ["u/api"]},
                        {"name": "d", "paths": ["d"], "layer": "down", "role": "base"}]}
    res = A.analyze(raw, spec)
    assert {c["id"] for c in res["candidates"]} >= {"M01", "M02", "M04"}
    assert res["summary"]["violation_edges"] == 1


def test_product_names_evidence_has_no_source_text(tmp_path):
    make_repo(tmp_path, {
        "platform/p.go": 'package platform\nvar tokens = map[string]string{"golf": "sk-live-SECRET"}\n',
        "app/a.go": 'package app\nconst name = "golf"\n',
    })
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    spec = {"modules": [{"name": "platform", "paths": ["platform"]}, {"name": "app", "paths": ["app"], "role": "product"}],
            "product_names": ["golf"]}
    res = A.analyze(raw, spec)
    m05 = [c for c in res["candidates"] if c["id"] == "M05"]
    assert [c["modules"] for c in m05] == [["platform"]]
    assert all("sk-live" not in e for e in m05[0]["evidence"]) and "golf" in m05[0]["evidence"][0]


def test_sql_in_multiline_raw_strings(tmp_path):
    make_repo(tmp_path, {
        "a/q.go": "package a\nvar q = `\nSELECT *\nFROM orders\n`\n",
        "b/q.py": 'Q = """\nSELECT 1\nFROM orders\n"""\n',
    })
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    assert {(t["src"], t["table"]) for t in raw["sql_tables"]} == {("a/q.go", "orders"), ("b/q.py", "orders")}


def test_unstable_dependency_threshold_boundary():
    raw = mk_raw({"z": 1, "a": 1, "b": 1, "x": 1, "y": 1, "w": 1},
                 [("z", "a/f.go"), ("a", "b/f.go"), ("b", "x/f.go"), ("b", "y/f.go"), ("b", "w/f.go")])
    spec = mk_map("z", "a", "b", "x", "y", "w")
    assert ("M07", ("a", "b")) in ids_of(A.analyze(raw, spec, thresholds={"unstable_delta": 0.25}))
    assert not any(i == "M07" for i, _ in ids_of(A.analyze(raw, spec, thresholds={"unstable_delta": 0.26})))


@pytest.mark.parametrize("ins,outs,expected", [(3, 5, True), (2, 5, False), (3, 4, False)])
def test_hub_threshold_boundary(ins, outs, expected):
    srcs = [f"i{k}" for k in range(ins)]
    dsts = [f"o{k}" for k in range(outs)]
    raw = mk_raw({n: 1 for n in ["h", *srcs, *dsts]}, [(s, "h/f.go") for s in srcs] + [("h", f"{d}/f.go") for d in dsts])
    assert (("M08", ("h",)) in ids_of(A.analyze(raw, mk_map("h", *srcs, *dsts)))) is expected


@pytest.mark.parametrize("sizes,expected", [([31, 23, 23, 23], True), ([30, 24, 23, 23], False), ([40, 30, 30], False)])
def test_god_module_threshold_boundary(sizes, expected):
    names = [f"m{k}" for k in range(len(sizes))]
    raw = mk_raw(dict(zip(names, sizes)), [])
    assert (("M09", ("m0",)) in ids_of(A.analyze(raw, mk_map(*names)))) is expected


def _commit(subject, files, sha=None):
    return {"sha": sha or subject[:7], "date": "2026-09-01T00:00:00+00:00", "subject": subject, "files": files}


def test_git_signals_cochange_fix_baseline_and_bulk_exclusion(go_raw):
    both = ["internal/common/config/config.go", "internal/shadow/service/s.go"]
    go_raw["commits"] = (
        [_commit("fix：修复影子同步", both) for _ in range(3)]
        + [_commit("fix(shadow): 修复重连", both) for _ in range(2)]
        + [_commit("feat: 新增指令", both)]
        + [_commit("feat: gateway", ["internal/gateway/g.go"]) for _ in range(10)]
        + [_commit("fix: ci 修改", [".gitlab-ci.yml"])]
        + [_commit("chore: 批量格式化", [f"internal/x/f{i}.go" for i in range(60)] + both)]
    )
    res = A.analyze(go_raw, GO_MAP)
    mods = {m["name"]: m for m in res["modules"]}
    assert mods["common"]["churn"] == 6 and mods["common"]["fixes"] == 5
    ids = ids_of(res)
    assert ("M11", ("common", "shadow")) in ids
    assert ("M12", ("shadow",)) in ids
    assert all(c["evidence"] for c in res["candidates"] if c["id"] == "M12")
    assert res["summary"]["commits_used"] == 16
    assert res["summary"]["repo_fix_ratio"] == round(5 / 16, 3)
    assert res["summary"]["change_amplification"] == round(12 / 11, 2)


def test_fix_hotspot_not_reported_when_module_matches_repo_baseline(go_raw):
    both = ["internal/common/config/config.go", "internal/shadow/service/s.go"]
    go_raw["commits"] = [_commit(f"fix: {i}", both) for i in range(8)]
    assert not any(i == "M12" for i, _ in ids_of(A.analyze(go_raw, GO_MAP)))


def test_fix_min_zero_does_not_divide_by_zero(go_raw):
    A.analyze(go_raw, GO_MAP, thresholds={"fix_min": 0})


def test_parse_git_log_record_format():
    a, b = "a" * 40, "b" * 64
    out = (f"\x00{a}\x1f2026-09-01T10:00:00+08:00\x1ffix: a\n\na/b.go\nc/d.go\n"
           f"\x00{b}\x1f2026-09-02T10:00:00+08:00\x1ffeat: b\n\ne.go\n")
    commits = A.parse_git_log(out)
    assert [c["sha"] for c in commits] == [a, b]
    assert commits[0]["files"] == ["a/b.go", "c/d.go"]


def test_parse_git_log_ignores_forged_separators_in_subject():
    real = "1" * 40
    out = (f"\x00{real}\x1f2026-09-01T10:00:00+08:00\x1ffix: x\x1e--output=/tmp/p\x1f2026-01-01T00:00:00Z\x1ffake\n\na.go\n"
           f"\x00--output=/tmp/q\x1f2026-01-01T00:00:00Z\x1ffix: fake\n\nb.go\n")
    commits = A.parse_git_log(out)
    assert [c["sha"] for c in commits] == [real] and commits[0]["files"] == ["a.go"]
    assert not re.search(r"[\x00-\x1f\x7f]", commits[0]["subject"])


def test_collect_does_not_trust_separators_in_commit_subjects(tmp_path):
    """提交标题里夹带分隔符，不能伪造出 sha——AI 复核时会执行 git show <sha>，伪造的 sha 可被当成 --output 写文件。"""
    require_git()
    repo = make_repo(tmp_path / "repo", {"a.go": "package a\n"})
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", f"fix: x\x1e--output={tmp_path}/pwned\x1f2026-01-01T00:00:00+00:00\x1ffix: fake")
    commits = A.collect(str(repo), use_jscpd=False)["commits"]
    assert len(commits) == 1 and commits[0]["files"] == ["a.go"]
    assert re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commits[0]["sha"])
    assert not re.search(r"[\x00-\x1f\x7f]", commits[0]["subject"])


def test_duplication_evidence_deduped_and_ratio_bounded(go_raw):
    clone = {"a": "internal/shadow/service/s.go", "a_start": 1, "b": "internal/common/config/config.go",
             "b_start": 1, "lines": 3}
    go_raw["clones"] = [clone, dict(clone, b="internal/gateway/g.go")]
    res = A.analyze(go_raw, GO_MAP)
    mods = {m["name"]: m for m in res["modules"]}
    assert mods["shadow"]["dup_lines"] == 3                  # 同一段只算一次
    m17 = {tuple(c["modules"]): c for c in res["candidates"] if c["id"] == "M17"}
    assert m17[("shadow",)]["evidence"]                        # 跨模块克隆也记到本模块证据
    assert all(m["dup_lines"] <= m["lines"] for m in res["modules"])
    assert "50.0%" in m17[("shadow",)]["detail"]


def test_duplication_between_two_modules(tmp_path):
    body = "".join(f"x{i} := {i}\n" for i in range(80))
    make_repo(tmp_path, {"a/a.go": "package a\n" + body, "b/b.go": "package b\n" + body})
    raw = A.collect(str(tmp_path), use_git=False, use_jscpd=False)
    raw["clones"] = [{"a": "a/a.go", "a_start": 2, "b": "b/b.go", "b_start": 2, "lines": 60}]
    res = A.analyze(raw, mk_map("a", "b"))
    assert ("M17", ("a", "b")) in ids_of(res)


# ---------------------------------------------------------------- 文件级：M18 超长文件、M19 文件缺陷热点

def test_long_file_and_file_fix_hotspot_candidates():
    raw = mk_raw({"a": 1200, "b": 999, "c": 50}, [])
    raw["files"]["a/gen.pb.go"] = {"lang": "go", "nloc": 5000, "lines": 5000, "test": False, "generated": True}
    raw["files"]["a/big_test.go"] = {"lang": "go", "nloc": 5000, "lines": 5000, "test": True, "generated": False}
    raw["files"]["vendor_x/v.go"] = {"lang": "go", "nloc": 5000, "lines": 5000, "test": False, "generated": False}
    raw["commits"] = (
        [_commit(f"fix: c {i}", ["c/f.go", "a/f.go"], sha=f"s{i}") for i in range(10)]
        + [_commit("feat: c", ["c/f.go"], sha="t1")]
        + [_commit("fix: 批量", ["c/f.go"] + [f"x/{i}.go" for i in range(60)], sha="bulk")]
        + [_commit("fix: 已删除的文件", ["c/gone.go"], sha=f"d{i}") for i in range(12)]
    )
    res = A.analyze(raw, mk_map("a", "b", "c"))
    fps = {c["fingerprint"] for c in res["candidates"] if c["id"] in ("M18", "M19")}
    assert fps == {"M18||a/f.go", "M19||a/f.go", "M19||c/f.go"}   # 生成物、测试、未归入模块、已删除的都不算
    m19 = next(c for c in res["candidates"] if c["fingerprint"] == "M19||c/f.go")
    assert m19["modules"] == ["c"] and "11 次提交中 10 次是 fix" in m19["detail"]
    assert m19["evidence"] == [f"s{i} fix: c {i}" for i in range(5)]          # 最近 5 次（git log 新的在前）
    assert res["hot_files"][1] == {"path": "c/f.go", "module": "c", "nloc": 50, "churn": 11, "fixes": 10,
                                   "long": False, "fix_hot": True}


def test_long_file_boundary_uses_effective_lines_and_map_thresholds():
    raw = mk_raw({"a": 1000, "b": 999}, [])
    raw["files"]["a/f.go"]["lines"] = 1500
    raw["files"]["b/f.go"]["lines"] = 5000        # 物理行多、有效代码不到线：不报

    def m18(res):
        return {c["fingerprint"] for c in res["candidates"] if c["id"] == "M18"}

    assert m18(A.analyze(raw, mk_map("a", "b"))) == {"M18||a/f.go"}
    assert m18(A.analyze(raw, mk_map("a", "b", thresholds={"long_file_nloc": 999}))) == {"M18||a/f.go", "M18||b/f.go"}


def test_hot_files_order_and_cap():
    raw = mk_raw({"a": 5000, "b": 1200, "c": 50}, [])     # a 只超长、最长、字母序最前
    raw["commits"] = ([_commit(f"fix: b {i}", ["b/f.go"], sha=f"b{i}") for i in range(10)]
                      + [_commit(f"fix: c {i}", ["c/f.go"], sha=f"c{i}") for i in range(12)])
    res = A.analyze(raw, mk_map("a", "b", "c"))
    assert [h["path"] for h in res["hot_files"]] == ["b/f.go", "c/f.go", "a/f.go"]   # 两者兼有 → fix 数 → 行数
    names = [f"m{i:03d}" for i in range(101)]
    res = A.analyze(mk_raw(dict.fromkeys(names, 10), []), mk_map(*names), thresholds={"long_file_nloc": 1})
    assert len(res["hot_files"]) == 100 and res["hot_files_count"] == 101


def test_file_level_thresholds_no_git_and_ignore_by_path():
    raw = mk_raw({"a": 1200, "b": 300}, [])
    res = A.analyze(raw, mk_map("a", "b"), thresholds={"long_file_nloc": 250})
    assert {c["fingerprint"] for c in res["candidates"] if c["id"] == "M18"} == {"M18||a/f.go", "M18||b/f.go"}
    assert all("没有可用的 git 提交" in c["detail"] for c in res["candidates"] if c["id"] == "M18")
    assert "M19" in res["unchecked_ids"] and "M18" not in res["unchecked_ids"]
    ign = [{"id": "M18", "modules": ["a"], "key": "a/f.go", "reason": "寄存器表"}]
    prev = {"candidates": [{"fingerprint": "M19||a/f.go"}, {"fingerprint": "M18||a/f.go"}]}
    res = A.analyze(raw, mk_map("a", "b"), ignore=ign, previous=prev, thresholds={"long_file_nloc": 250})
    assert [c["fingerprint"] for c in res["ignored"]] == ["M18||a/f.go"]
    assert res["comparison"]["unchecked"] == ["M19||a/f.go"] and res["comparison"]["fixed"] == []
    # file_fix_min=0 不除零，也不把没有 fix 的文件算成缺陷热点
    assert A.analyze(raw, mk_map("a", "b"), thresholds={"file_fix_min": 0})["hot_files_count"] == 1


def test_ignore_by_file_path_is_precise_and_applies_to_hot_files():
    raw = mk_raw({"a": 1500}, [])
    raw["files"]["a/regs.go"] = {"lang": "go", "nloc": 20000, "lines": 20000, "test": False, "generated": False}
    raw["commits"] = [_commit(f"fix: {i}", ["a/f.go"], sha=f"s{i}") for i in range(10)]
    ign = [{"id": "M18", "modules": ["a"], "key": "a/regs.go", "reason": "寄存器表"},
           {"id": "M18", "modules": ["a"], "key": "a/f.go", "reason": "只忽略超长，fix 多照报"}]
    res = A.analyze(raw, mk_map("a"), ignore=ign)
    assert not any(c["id"] == "M18" for c in res["candidates"]) and "M19||a/f.go" in {c["fingerprint"] for c in res["candidates"]}
    assert res["hot_files"] == [{"path": "a/f.go", "module": "a", "nloc": 1500, "churn": 10, "fixes": 10,
                                 "long": False, "fix_hot": True}]                  # 被忽略的不再进热点文件
    assert res["hot_files_count"] == 1
    res = A.analyze(raw, mk_map("a"), ignore=ign[:1])
    assert "M18||a/f.go" in {c["fingerprint"] for c in res["candidates"]}          # 只挡住写了路径的那个
    both = ign + [{"id": "M19", "modules": ["a"], "key": "a/f.go", "reason": "已排期重构"}]
    res = A.analyze(raw, mk_map("a"), ignore=both)
    assert res["hot_files"] == [] and res["hot_files_count"] == 0                   # 两项都被挡的文件整条不列
    res = A.analyze(raw, mk_map("a"), ignore=[{"id": "M18", "modules": ["a"], "key": "", "reason": "空路径"}])
    assert len(res["invalid_ignores"]) == 1                                         # 文件级空路径挡不住任何文件：判无效


def test_file_level_fingerprint_survives_module_map_changes():
    raw = mk_raw({"a": 10}, [])
    raw["files"]["a/sub/big.go"] = {"lang": "go", "nloc": 3000, "lines": 3000, "test": False, "generated": False}
    prev = A.analyze(raw, mk_map("a"))
    assert "M18||a/sub/big.go" in {c["fingerprint"] for c in prev["candidates"]}
    spec = {"modules": [{"name": "a", "paths": ["a"], "exclude": ["a/sub"]}, {"name": "sub", "paths": ["a/sub"]}]}
    res = A.analyze(raw, spec, previous=prev)
    assert "M18||a/sub/big.go" in res["comparison"]["still"] and not res["comparison"]["fixed"]
    ign = [{"id": "M18", "modules": ["a"], "key": "a/sub/big.go", "reason": "旧地图里写的"}]
    assert [c["fingerprint"] for c in A.analyze(raw, spec, ignore=ign)["ignored"]] == ["M18||a/sub/big.go"]


def test_window_with_only_bulk_commits_counts_as_unchecked():
    raw = mk_raw({"a": 10}, [])
    raw["commits"] = [_commit("fix: 批量", ["a/f.go"] + [f"x/{i}.go" for i in range(60)])]
    res = A.analyze(raw, mk_map("a"), previous={"candidates": [{"fingerprint": "M19||a/f.go"}]})
    assert {"M11", "M12", "M19"} <= set(res["unchecked_ids"])
    assert res["comparison"]["unchecked"] == ["M19||a/f.go"] and res["comparison"]["fixed"] == []
    assert any("批量提交" in n for n in res["notes"])


# ---------------------------------------------------------------- 忽略名单、对比、阈值

def test_ignore_requires_reason_and_comparison(go_raw, tmp_path):
    ign = tmp_path / "ignore.yaml"
    ign.write_text("- id: M06\n  modules: [common, shadow]\n  reason: 已排期拆分\n  by: someone\n"
                   "- id: M14\n  modules: [common]\n", encoding="utf-8")
    res = A.analyze(go_raw, GO_MAP, ignore=A.load_ignore(str(ign)),
                    previous={"candidates": [{"fingerprint": "M09|x|"}, {"fingerprint": "M13|gateway+shadow|devices"}]})
    assert all(c["id"] != "M06" for c in res["candidates"])
    assert any(c["id"] == "M14" for c in res["candidates"])     # 没写 reason，不生效
    assert len(res["invalid_ignores"]) == 1
    assert res["comparison"]["fixed"] == ["M09|x|"]
    assert res["comparison"]["still"] == ["M13|gateway+shadow|devices"]
    assert "M14|common|" in res["comparison"]["new"]


def test_newly_ignored_is_not_fixed_and_renames_are_followed(go_raw):
    spec = json.loads(json.dumps(GO_MAP))
    spec["modules"][2]["renamed_from"] = ["oldshadow"]
    ignore = [{"id": "M14", "modules": ["common"], "reason": "名实相符"}]
    prev = {"candidates": [{"fingerprint": "M14|common|"}, {"fingerprint": "M06|common+oldshadow|"}]}
    comp = A.analyze(go_raw, spec, ignore=ignore, previous=prev)["comparison"]
    assert "M14|common|" not in comp["fixed"]
    assert "M06|common+shadow|" in comp["still"] and not comp["fixed"]
    spec["modules"][2]["renamed_from"] = "oldshadow"               # 单个字符串也接受
    res = A.analyze(go_raw, spec, previous=prev, ignore=[{"id": "M06", "modules": ["common", "oldshadow"], "reason": "已排期"}])
    assert res["renames"] == {"oldshadow": "shadow"}
    assert any(c["fingerprint"] == "M06|common+shadow|" for c in res["ignored"])   # 忽略名单里的旧名也跟着改名


def test_minimal_ignore_parser_matches_yaml():
    yaml = pytest.importorskip("yaml")
    text = ("# 团队决定不改的\n"
            "- id: M13\n  modules: [gateway, shadow]\n  key: devices\n  reason: '只读报表同步'\n"
            "- id: M09\n  modules:\n    - shadow\n  reason: see issue #123\n")
    assert A.parse_ignore_minimal(text) == yaml.safe_load(text)


def test_yaml_alias_bomb_in_ignore_is_bounded(go_raw, tmp_path):
    lines = ["- &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for k in range(1, 7):
        lines.append(f"- &a{k} [" + ", ".join([f"*a{k - 1}"] * 10) + "]")
    ign = tmp_path / "bomb.yaml"
    ign.write_text("\n".join(lines) + "\n", encoding="utf-8")
    t = time.perf_counter()
    res = A.analyze(go_raw, GO_MAP, ignore=A.load_ignore(str(ign)))
    out = tmp_path / "f.json"
    A._dump(res, str(out))
    assert time.perf_counter() - t < 5 and out.stat().st_size < 200_000


def test_unchecked_signals_are_not_reported_as_fixed(go_raw):
    prev = {"candidates": [{"fingerprint": f} for f in ("M12|shadow|", "M17|shadow|", "M11|common+shadow|", "M09|x|")]}
    res = A.analyze(go_raw, GO_MAP, previous=prev)          # 本期无 git、无 jscpd
    assert {"M11", "M12", "M17", "M03", "M05"} <= set(res["unchecked_ids"])
    assert res["comparison"]["fixed"] == ["M09|x|"]
    assert res["comparison"]["unchecked"] == ["M11|common+shadow|", "M12|shadow|", "M17|shadow|"]
    item = {"title": "热点", "fingerprints": ["M12|a|"], "status": "confirmed"}
    comp = A.compare_reviewed({"items": [item]}, {"items": []}, unchecked_ids={"M12"})
    assert comp["fixed"] == [] and comp["unchecked"] == ["热点"]


def test_malformed_module_map_warns_or_errors_cleanly(go_raw, tmp_path):
    spec = {"modules": [{"name": "common", "paths": ["internal/common"], "role": ["base"], "layer": ["x"]}],
            "allow": [["a", ["b"]]], "isolated": [[["x"]]]}
    res = A.analyze(go_raw, spec)
    assert len(res["map_warnings"]) == 2
    raw_p, mp = tmp_path / "raw.json", tmp_path / "m.json"
    A._dump(go_raw, str(raw_p))
    mp.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        A.main(["analyze", str(raw_p), "--modules", str(mp), "--out", str(tmp_path / "f.json")])
    with pytest.raises(SystemExit):
        A.main(["collect", str(tmp_path / "no-such-repo"), "--out", str(tmp_path / "r.json"), "--no-jscpd"])


def test_thresholds_persist_in_map_and_cli_overrides(go_raw):
    spec = dict(GO_MAP, thresholds={"hub_in": 1, "hub_out": 1})
    assert ("M08", ("common",)) in ids_of(A.analyze(go_raw, spec))
    assert ("M08", ("common",)) not in ids_of(A.analyze(go_raw, spec, thresholds={"hub_out": 5}))
    res = A.analyze(go_raw, spec, previous={"candidates": [], "thresholds": {"hub_in": 3}})
    assert any("阈值" in n for n in res["notes"])


def test_map_validation_warns_about_unknown_layer(go_raw):
    spec = {"layers": ["sdk"], "modules": [{"name": "common", "paths": ["internal/common"], "layer": "sdk "}]}
    assert A.analyze(go_raw, spec)["map_warnings"]


def test_compare_reviewed_results():
    prev = {"items": [{"title": "A", "fingerprints": ["F1"], "status": "confirmed"},
                      {"title": "B", "fingerprints": ["F2"], "status": "confirmed"},
                      {"title": "E", "fingerprints": ["F4"], "status": "confirmed"}]}
    cur = {"items": [{"title": "A2", "fingerprints": ["F1", "F9"], "status": "confirmed"},
                     {"title": "C", "fingerprints": ["F3"], "status": "confirmed"},
                     {"title": "D", "fingerprints": ["F2"], "status": "rejected"}]}
    comp = A.compare_reviewed(prev, cur)
    assert comp["new"] == ["C"] and comp["still"] == ["A2"]
    assert comp["fixed"] == ["E"] and comp["reclassified"] == ["B"]
    # 本期复核漏写了，但候选还在：不能算「已修」
    comp = A.compare_reviewed({"items": [prev["items"][2]]}, {"items": []}, candidates={"F4"})
    assert comp["fixed"] == [] and comp["reclassified"] == ["E"]


# ---------------------------------------------------------------- 外部进程

def test_run_isolated_kills_whole_process_group(tmp_path):
    pidfile = tmp_path / "pid"
    rc, _, timed_out = A._run_isolated(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], str(tmp_path),
                                       dict(os.environ), 1)
    assert timed_out
    pid = int(pidfile.read_text().strip())
    time.sleep(0.3)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    # 容器里 PID 1 不一定回收孤儿进程：僵尸（Z）也算已杀掉
    state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    assert state == "Z"


def test_run_jscpd_runs_isolated_from_scanned_repo(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo", {".npmrc": "script-shell=./pwn.sh\n", "a/x.go": "package a\n"})
    monkeypatch.setenv("npm_config_script_shell", "./pwn.sh")
    monkeypatch.setattr(A.shutil, "which", lambda name: "/usr/bin/npx" if name == "npx" else None)
    seen = {}

    def fake_run(cmd, cwd, env, timeout):
        seen.update(cmd=cmd, cwd=cwd, env=env, pkg=os.path.exists(os.path.join(cwd, "package.json")))
        out = cmd[cmd.index("--output") + 1]
        os.makedirs(out, exist_ok=True)
        # 一个绝对路径、一个相对 jscpd 工作目录的路径（真实 jscpd 不加 --absolute 时的格式）
        with open(os.path.join(out, "jscpd-report.json"), "w", encoding="utf-8") as fh:
            json.dump({"duplicates": [{"firstFile": {"name": str(repo / "a/x.go"), "start": 1},
                                       "secondFile": {"name": os.path.relpath(repo / "b/y.go", cwd), "start": 3},
                                       "lines": 12}]}, fh)
        return 0, "", False

    monkeypatch.setattr(A, "_run_isolated", fake_run)
    clones, note = A.run_jscpd(str(repo), 10, 70, 60)
    assert note is None and clones == [{"a": "a/x.go", "a_start": 1, "b": "b/y.go", "b_start": 3, "lines": 12}]
    assert not os.path.abspath(seen["cwd"]).startswith(str(repo))
    assert f"jscpd@{A.JSCPD_VERSION}" in seen["cmd"] and A.JSCPD_VERSION.count(".") == 2
    assert "--noSymlinks" in seen["cmd"] and "--config" in seen["cmd"] and "--absolute" in seen["cmd"]
    assert seen["pkg"]                                              # npx 不再向上级目录找 node_modules
    assert seen["cmd"][-1] == str(repo)
    assert not any(k.lower().startswith("npm_config_script") for k in seen["env"])


def test_run_jscpd_failure_paths(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, {"a.go": "package a\n"})
    monkeypatch.setattr(A.shutil, "which", lambda name: None)
    assert A.run_jscpd(str(repo), 10, 70, 5) == (None, "npx not found; M17 skipped")
    monkeypatch.setattr(A.shutil, "which", lambda name: "/usr/bin/npx")
    monkeypatch.setattr(A, "_run_isolated", lambda *a: (None, "", True))
    assert "timeout" in A.run_jscpd(str(repo), 10, 70, 5)[1]

    def broken(cmd, cwd, env, timeout):
        out = cmd[cmd.index("--output") + 1]
        os.makedirs(out, exist_ok=True)
        Path(out, "jscpd-report.json").write_text("{not json", encoding="utf-8")
        return 0, "", False

    monkeypatch.setattr(A, "_run_isolated", broken)
    assert "unreadable" in A.run_jscpd(str(repo), 10, 70, 5)[1]


def test_git_log_subdir_no_renames_default_window_and_empty_window(tmp_path):
    require_git()
    repo = make_repo(tmp_path / "mono", {"proj/a/x.go": "package a\n", "other/y.go": "package y\n"})
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: init")
    git(repo, "mv", "proj/a/x.go", "proj/a/z.go")
    git(repo, "commit", "-qm", "refactor: rename")
    commits, note = A.git_log(str(repo / "proj"), "6 months ago")
    assert note is None and len(commits) == 2
    assert all(not f.startswith("proj/") and not f.startswith("other/") for c in commits for f in c["files"])
    assert set(commits[0]["files"]) == {"a/x.go", "a/z.go"}   # --no-renames：删除与新增都列出
    stale = make_repo(tmp_path / "stale", {"a.go": "package a\n"})   # 本地克隆很久没更新
    git(stale, "init", "-q")
    git(stale, "add", ".")
    git(stale, "commit", "-qm", "feat: old", date="2020-01-01T00:00:00+00:00")
    commits, note = A.git_log(str(stale), "6 months ago")
    assert commits == [] and "没有提交" in note and "2020-01-01" in note


def test_cli_end_to_end_with_git_ignore_previous_and_compare(tmp_path, capsys):
    """L3：真实 git 仓 → collect → analyze（忽略名单 + 上期对比）→ compare。"""
    require_git()
    repo = make_repo(tmp_path / "repo", GO_FILES)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: init")
    (repo / "internal/shadow/service/s.go").write_text(GO_FILES["internal/shadow/service/s.go"] + "// fix\n",
                                                      encoding="utf-8")
    git(repo, "commit", "-qam", "fix：影子同步")
    (repo / ".arch-ignore.yaml").write_text(
        "- id: M09\n  modules: [common]\n  reason: 核心底座\n  date: 2026-09-11\n"
        "- id: M14\n  modules: [common]\n  date: 2026-09-11\n", encoding="utf-8")
    run = tmp_path / "run"
    mp = run / "module-map.json"
    run.mkdir()
    mp.write_text(json.dumps(GO_MAP), encoding="utf-8")
    assert A.main(["collect", str(repo), "--out", str(run / "raw.json"), "--no-jscpd"]) == 0
    raw = json.loads((run / "raw.json").read_text(encoding="utf-8"))
    assert len(raw["commits"]) == 2 and raw["head"]
    assert A.main(["analyze", str(run / "raw.json"), "--modules", str(mp), "--ignore", str(repo / ".arch-ignore.yaml"),
                   "--out", str(run / "f1.json")]) == 0
    f1 = json.loads((run / "f1.json").read_text(encoding="utf-8"))
    assert len(f1["invalid_ignores"]) == 1 and all(c["id"] != "M09" for c in f1["candidates"])
    assert A.main(["analyze", str(run / "raw.json"), "--modules", str(mp), "--previous", str(run / "f1.json"),
                   "--out", str(run / "f2.json")]) == 0
    f2 = json.loads((run / "f2.json").read_text(encoding="utf-8"))
    assert f2["comparison"]["new"] == ["M09|common|"]
    assert A.main(["analyze", str(run / "raw.json"), "--modules", str(mp), "--threshold", "file_fix_min=1",
                   "--out", str(run / "f3.json")]) == 0
    f3 = json.loads((run / "f3.json").read_text(encoding="utf-8"))
    hot = {h["path"]: h for h in f3["hot_files"]}
    assert hot["internal/shadow/service/s.go"]["fixes"] == 1 and hot["internal/shadow/service/s.go"]["fix_hot"]
    assert "M19||internal/shadow/service/s.go" in {c["fingerprint"] for c in f3["candidates"]}
    rv1 = {"items": [{"title": "环", "fingerprints": ["M06|common+shadow|"], "status": "confirmed"}]}
    rv2 = {"items": [{"title": "环", "fingerprints": ["M06|common+shadow|", "M04|common+shadow|"], "status": "confirmed"}]}
    (run / "r1.json").write_text(json.dumps(rv1), encoding="utf-8")
    (run / "r2.json").write_text(json.dumps(rv2), encoding="utf-8")
    assert A.main(["compare", "--previous", str(run / "r1.json"), "--current", str(run / "r2.json"),
                   "--findings", str(run / "f2.json"), "--out", str(run / "cmp.json")]) == 0
    assert json.loads((run / "cmp.json").read_text(encoding="utf-8"))["still"] == ["环"]
    err = capsys.readouterr().err
    assert "invalid" in err or "ignore" in err


def test_cli_rejects_bad_arguments(go_raw, tmp_path):
    raw_p = tmp_path / "raw.json"
    A._dump(go_raw, str(raw_p))
    for args in (["--threshold", "hub_out=1.5"], ["--threshold", "nope=1"], ["--previous", str(tmp_path / "missing.json")]):
        with pytest.raises(SystemExit):
            A.main(["analyze", str(raw_p), "--out", str(tmp_path / "f.json"), *args])


def test_cli_diagnostics_go_to_stderr(go_raw, tmp_path, capsys):
    raw_p = tmp_path / "raw.json"
    A._dump(go_raw, str(raw_p))
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps({"modules": [{"name": "shadow", "paths": ["internal/shadow"]}]}), encoding="utf-8")
    assert A.main(["analyze", str(raw_p), "--modules", str(mp), "--out", str(tmp_path / "f.json")]) == 0
    captured = capsys.readouterr()
    assert "warning" in captured.err and "warning" not in captured.out


def test_draft_modules_use_container_dirs(go_raw):
    names = {tuple(m["paths"]) for m in go_raw["draft_modules"]}
    assert ("internal/common",) in names and ("internal/shadow",) in names and ("cmd/server",) in names
