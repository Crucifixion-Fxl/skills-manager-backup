#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6.0"]  # 可选：只用于读忽略名单，缺失时退回内置简易解析
# ///
"""架构坏味道体检（architecture-smell-scan）的证据采集、分析与对比脚本。

三个子命令：
  collect  抽取仓库的文件级内部依赖（带行号）、代码量、git 历史、重复片段、
           SQL 表引用、外部依赖，并给出模块地图草稿
  analyze  按 AI 确认的模块地图聚合成模块依赖图，计算指标，产出候选发现（M 编号）
  compare  对比两期 AI 复核后的结果（reviewed.json），得出新增 / 已修 / 仍在

只依赖 Python 标准库（有 PyYAML 时用它读忽略名单）；重复代码检测经 npx 调 jscpd，可选。
被扫仓的一切内容都当作不可信输入：不在被扫仓目录里执行任何外部程序的配置加载。
规则正本：public/dev-standards/architecture/architecture-smells.html
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import json
import os
import posixpath
import re
import reprlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from itertools import combinations

SCHEMA_VERSION = 2
JSCPD_VERSION = "4.3.0"
MAX_FILE_BYTES = 2_000_000      # 超过的代码文件按生成物处理，不读
MAX_LINE_CHARS = 2000           # 超长行（压缩代码、数据）不做正则解析
MAX_SUBJECT_CHARS = 500
MAX_IGNORE_BYTES = 256_000
GIT_TIMEOUT = 600

LANG_BY_EXT = {
    ".go": "go",
    ".py": "python",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts", ".js": "ts", ".jsx": "ts",
    ".mjs": "ts", ".cjs": "ts", ".vue": "ts",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".dart": "dart",
    ".swift": "swift",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".cxx": "c", ".hpp": "c", ".hh": "c",
}
TS_EXTS = [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue"]
JS_TO_TS = {".js": [".ts", ".tsx"], ".jsx": [".tsx"], ".mjs": [".mts"], ".cjs": [".cts"]}
SKIP_DIRS = {
    ".git", "vendor", "node_modules", "third_party", "3rdparty", "third-party", "build", "dist",
    "out", "target", ".dart_tool", "Pods", "DerivedData", "__pycache__", ".venv", "venv",
    ".gradle", ".idea", "coverage", ".next", ".turbo",
}
GENERATED_PATTERNS = [
    "*.pb.go", "*.gen.go", "*_generated.*", "*.g.dart", "*.freezed.dart", "*.gr.dart",
    "*_pb2.py", "*_pb2_grpc.py", "*.pb.h", "*.pb.cc", "*.min.*", "*.bundle.js", "*.chunk.js",
]
TEST_DIR_RE = re.compile(
    r"^(tests?|__tests__|androidtest|integration_tests?|unit_?tests?|gtest|testdata|tests?[_-].+|.+[_-]tests?)$",
    re.I,
)
XCODE_TEST_DIR_RE = re.compile(r"^.+Tests$")
TEST_NAME_RE = re.compile(
    r"((_test|_tests|_unittest)\.\w+$|^test_.*\.\w+$|\.(test|spec)\.[cm]?[jt]sx?$)", re.I
)
TEST_CLASS_RE = re.compile(r"Tests?\.(java|kt|swift)$")
BUILD_FILES = {
    "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pubspec.yaml",
    "Package.swift", "pyproject.toml", "setup.py", "BUILD", "BUILD.bazel", "CMakeLists.txt",
}
CONTAINER_DIRS = {
    "internal", "pkg", "cmd", "src", "lib", "app", "apps", "packages", "modules",
    "services", "components", "features", "libs",
}
VAGUE_NAMES = {
    "common", "commons", "util", "utils", "misc", "helper", "helpers", "base", "core",
    "manager", "managers", "shared", "other", "others", "new", "tmp", "temp", "stuff",
    "lib", "libs", "v2", "general", "public",
}
ROLES = {"base", "root", "business", "product", "leaf"}
SCRIPT_IDS = {"M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08", "M09", "M11", "M12", "M13", "M14", "M17",
              "M18", "M19"}
GIT_IDS = ("M11", "M12", "M19")   # 依赖 git 历史的编号
FILE_IDS = {"M18", "M19"}         # 文件级：身份是文件路径，指纹不含模块
FIX_RE = re.compile(r"^\s*(fix|hotfix|bugfix)(\([^)]*\))?!?\s*[:：]", re.I)
FEAT_RE = re.compile(r"^\s*feat(\([^)]*\))?!?\s*[:：]", re.I)
LOG_HEADER = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\x1f[^\x1f\n]*\x1f")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
JSCPD_FORMATS = (
    "go,python,typescript,javascript,tsx,jsx,java,kotlin,dart,swift,c,cpp,c-header,cpp-header"
)

DEFAULT_THRESHOLDS = {
    "bulk_commit_files": 50,   # 一次改动超过这么多文件的提交不计入 git 统计
    "unstable_delta": 0.2,     # M07：被依赖方 I − 依赖方 I
    "hub_in": 3,               # M08：入度
    "hub_out": 5,              # M08：出度
    "god_share": 0.30,         # M09：模块代码占已归入模块代码的比例
    "god_min_modules": 4,      # M09：仓内模块数下限
    "cochange_min": 5,         # M11：共同修改次数
    "coupling_min": 0.5,       # M11：耦合度
    "fix_min": 5,              # M12：fix 提交次数
    "fix_ratio": 0.4,          # M12：fix 占比
    "fix_margin": 0.10,        # M12：比全仓 fix 基线至少高出这么多
    "dup_ratio": 0.05,         # M17：模块重复率（重复行 ÷ 物理行）
    "dup_pair_lines": 50,      # M17：两模块间重复行数
    "long_file_nloc": 1000,    # M18：单个文件的有效代码行
    "file_fix_min": 10,        # M19：改到该文件的 fix 提交次数
}

_SHORT = reprlib.Repr()
_SHORT.maxlevel, _SHORT.maxlist, _SHORT.maxdict, _SHORT.maxstring, _SHORT.maxother = 3, 5, 5, 80, 80


# ---------------------------------------------------------------- 通用小工具

def pj(*parts: str) -> str:
    """拼接仓内相对路径，忽略空段和 '.'。"""
    return "/".join(p.strip("/") for p in parts if p not in ("", "."))


def parent_dir(path: str) -> str:
    d = posixpath.dirname(path)
    return "" if d in ("", ".") else d


def norm_rel(path: str | None) -> str | None:
    """规范化仓内相对路径；越出仓库根返回 None。"""
    if path is None:
        return None
    n = posixpath.normpath(path) if path else ""
    if n in (".", ""):
        return ""
    if n.startswith("../") or n == "..":
        return None
    return n


def is_generated(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in GENERATED_PATTERNS)


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    if any(TEST_DIR_RE.match(seg) or XCODE_TEST_DIR_RE.match(seg) for seg in parts[:-1]):
        return True
    if "/src/test/" in f"/{path}":
        return True
    name = parts[-1]
    return bool(TEST_NAME_RE.search(name) or TEST_CLASS_RE.search(name))


def count_nloc(text: str, lang: str) -> int:
    """有效代码行：去掉空行和整行注释（近似）。"""
    n = 0
    in_block = False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if lang == "python":
            if s.startswith("#"):
                continue
        else:
            if in_block:
                if "*/" in s:
                    in_block = False
                continue
            if s.startswith("/*"):
                if "*/" not in s[2:]:
                    in_block = True
                continue
            if s.startswith("//"):
                continue
        n += 1
    return n


def count_lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def path_matches(path: str, mpath: str) -> bool:
    if mpath == ".":
        return "/" not in path
    if mpath == "":
        return True
    mp = mpath.rstrip("/")
    return path == mp or path.startswith(mp + "/")


def short_repr(obj) -> str:
    return _SHORT.repr(obj)


def say(msg: str) -> None:
    """诊断信息走 stderr；无法编码的字符转义输出。"""
    sys.stderr.write(msg.encode("utf-8", "backslashreplace").decode("utf-8") + "\n")


# ---------------------------------------------------------------- import 抽取

GO_SINGLE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"')
GO_BLOCK_START = re.compile(r"^\s*import\s*\(\s*(?://.*)?$")
GO_BLOCK_LINE = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"')

PY_FROM = re.compile(r"^\s*from\s+(\.*)(\w[\w.]*)?\s+import\s+(.+)$")
PY_IMPORT = re.compile(r"^\s*import\s+(.+)$")

TS_FROM = re.compile(r"""(?:^|[\s}])from\s+['"]([^'"]+)['"]""")
TS_SIDE = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""")
TS_CALL = re.compile(r"""\b(?:require|import)\(\s*['"]([^'"]+)['"]\s*\)""")

JAVA_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)")
JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)")

DART_IMPORT = re.compile(r"""^\s*(?:import|export|part)\s+['"]([^'"]+)['"]""")
SWIFT_IMPORT = re.compile(
    r"^\s*(?:@testable\s+)?import\s+(?:(?:class|struct|enum|protocol|func|var|let|typealias)\s+)?(\w+)"
)
C_INCLUDE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]')


def go_imports(text: str):
    out = []
    in_block = False
    for i, line in enumerate(text.splitlines(), 1):
        if in_block:
            if line.strip().startswith(")"):
                in_block = False
                continue
            m = GO_BLOCK_LINE.match(line)
            if m:
                out.append((i, m.group(1)))
            continue
        if GO_BLOCK_START.match(line):
            in_block = True
            continue
        m = GO_SINGLE.match(line)
        if m:
            out.append((i, m.group(1)))
    return out


def _py_names(part: str) -> list[str]:
    part = part.replace("(", " ").replace(")", " ").replace("\\", " ")
    names = []
    for chunk in part.split(","):
        name = chunk.strip().split(" as ")[0].strip()
        if re.fullmatch(r"[\w*]+", name or ""):
            names.append(name)
    return names


def py_imports(text: str):
    out = []
    pending = None
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if pending is not None:
            pending["names"].extend(_py_names(code.split(")")[0]))
            if ")" in code:
                out.append((pending.pop("line"), pending))
                pending = None
            continue
        m = PY_FROM.match(code)
        if m:
            level, mod, names = len(m.group(1)), m.group(2) or "", m.group(3).strip()
            if names.startswith("(") and ")" not in names:
                pending = {"line": i, "level": level, "module": mod, "names": _py_names(names)}
                continue
            out.append((i, {"level": level, "module": mod, "names": _py_names(names)}))
            continue
        m = PY_IMPORT.match(code)
        if m:
            for part in m.group(1).split(","):
                mod = part.strip().split(" as ")[0].strip()
                if re.fullmatch(r"[\w.]+", mod or ""):
                    out.append((i, {"level": 0, "module": mod, "names": []}))
    return out


def ts_imports(text: str):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("//") or s.startswith("*"):
            continue
        found = []
        for rx in (TS_FROM, TS_SIDE, TS_CALL):
            found.extend(rx.findall(line))
        for spec in dict.fromkeys(found):
            out.append((i, spec))
    return out


def java_imports(text: str):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = JAVA_IMPORT.match(line)
        if m and m.group(1).rstrip("."):
            out.append((i, m.group(1).rstrip(".")))
    return out


def java_package(text: str) -> str | None:
    for line in text.splitlines()[:60]:
        m = JAVA_PACKAGE.match(line)
        if m:
            return m.group(1).rstrip(".")
    return None


def simple_imports(rx):
    def extract(text: str):
        out = []
        for i, line in enumerate(text.splitlines(), 1):
            m = rx.match(line)
            if m:
                out.append((i, m.group(m.lastindex)))
        return out
    return extract


def c_includes(text: str):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = C_INCLUDE.match(line)
        if m:
            out.append((i, (m.group(1), m.group(2))))
    return out


def strip_jsonc(text: str) -> str:
    """去掉 JSON 里的 // 与 /* */ 注释（跳过字符串内容）和尾逗号。"""
    out = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


# ---------------------------------------------------------------- 仓库索引与解析

class RepoIndex:
    def __init__(self, root: str, files: dict[str, dict], texts: dict[str, str], all_files: set[str],
                 notes: list[str]):
        self.root = root
        self.files = files
        self.texts = texts
        self.all_files = all_files
        self.notes = notes
        self.dirs: set[str] = set()
        for f in all_files:
            d = parent_dir(f)
            while d:
                self.dirs.add(d)
                d = parent_dir(d)
        self.go_modules = self._go_modules()
        self.py_roots = self._py_roots()
        self.ts_configs = self._ts_configs()
        self.java_packages = self._java_packages()
        self.dart_packages = self._dart_packages()
        self.swift_modules = self._swift_modules()
        self.suffix_index = self._suffix_index()

    def _read(self, rel: str) -> str:
        try:
            with open(os.path.join(self.root, rel), encoding="utf-8", errors="ignore") as fh:
                return fh.read(MAX_FILE_BYTES)
        except OSError:
            return ""

    def _go_modules(self):
        mods = []
        for f in self.all_files:
            if f.rsplit("/", 1)[-1] == "go.mod":
                m = re.search(r"^module\s+(\S+)", self._read(f), re.M)
                if m:
                    mods.append((m.group(1), parent_dir(f)))
        return sorted(mods, key=lambda x: (-len(x[0]), x[0]))

    def _py_roots(self):
        roots = [""]
        if "src" in self.dirs:
            roots.append("src")
        for f in sorted(self.all_files):
            if f.rsplit("/", 1)[-1] in ("pyproject.toml", "setup.py", "setup.cfg"):
                d = parent_dir(f)
                for r in (d, pj(d, "src")):
                    if (r == "" or r in self.dirs) and r not in roots:
                        roots.append(r)
        return roots

    def _ts_configs(self):
        """每个 tsconfig / jsconfig 所在目录 → 该目录生效的路径别名；近的配置优先。"""
        configs: dict[str, list] = {}
        for f in sorted(self.all_files):
            name = f.rsplit("/", 1)[-1]
            if f.count("/") > 3 or not (re.fullmatch(r"tsconfig(\.[\w-]+)?\.json", name) or name == "jsconfig.json"):
                continue
            try:
                data = json.loads(strip_jsonc(self._read(f)))
                opts = data.get("compilerOptions") or {}
                if not isinstance(opts, dict):
                    raise TypeError("compilerOptions")
                base_url, paths = opts.get("baseUrl", "."), opts.get("paths") or {}
                if not isinstance(base_url, str) or not isinstance(paths, dict):
                    raise TypeError("baseUrl/paths")
            except (ValueError, TypeError, AttributeError, RecursionError) as exc:
                self.notes.append(f"tsconfig 解析失败，已跳过其路径别名：{f}（{type(exc).__name__}）")
                continue
            cdir = parent_dir(f)
            base = norm_rel(pj(cdir, base_url))
            if base is None:
                continue
            aliases = []
            for key, targets in paths.items():
                if not isinstance(key, str) or not isinstance(targets, list):
                    continue
                wildcard = key.endswith("*")
                prefix = key[:-1] if wildcard else key
                dirs = [norm_rel(pj(base, t[:-1] if t.endswith("*") else t)) for t in targets if isinstance(t, str)]
                aliases.append((prefix, [d for d in dirs if d is not None], wildcard))
            configs.setdefault(cdir, []).extend(aliases)
        for aliases in configs.values():
            aliases.sort(key=lambda a: (-len(a[0]), a[0]))
        return sorted(configs.items(), key=lambda kv: (-len(kv[0]), kv[0]))

    def _java_packages(self):
        pkgs: dict[str, set[str]] = defaultdict(set)
        for f, meta in self.files.items():
            if meta["lang"] in ("java", "kotlin"):
                p = java_package(self.texts.get(f, ""))
                if p:
                    pkgs[p].add(parent_dir(f))
        return pkgs

    def _dart_packages(self):
        pk = {}
        for f in sorted(self.all_files):
            if f.rsplit("/", 1)[-1] == "pubspec.yaml":
                m = re.search(r"^name:\s*([\w_]+)", self._read(f), re.M)
                if m:
                    pk[m.group(1)] = pj(parent_dir(f), "lib")
        return pk

    def _swift_modules(self):
        """只认 SwiftPM 的 Sources/<Target>；普通目录名不当模块（避免与系统框架同名误连）。"""
        by_name: dict[str, set[str]] = defaultdict(set)
        for f, meta in self.files.items():
            if meta["lang"] != "swift":
                continue
            parts = parent_dir(f).split("/")
            if "Sources" in parts:
                idx = parts.index("Sources")
                if idx + 1 < len(parts):
                    by_name[parts[idx + 1]].add("/".join(parts[: idx + 2]))
        return {k: sorted(v) for k, v in by_name.items()}

    def _suffix_index(self):
        idx: dict[str, set[str]] = defaultdict(set)
        for f in self.all_files:
            parts = f.split("/")
            for k in range(1, min(4, len(parts)) + 1):
                idx["/".join(parts[-k:])].add(f)
        return idx

    # -- 各语言解析：返回 (内部目标路径列表, 外部依赖名或 None)

    def resolve(self, src: str, lang: str, spec):
        return getattr(self, f"_resolve_{lang}")(src, spec)

    def _resolve_go(self, src, spec):
        for mod, moddir in self.go_modules:
            if spec == mod or spec.startswith(mod + "/"):
                target = pj(moddir, spec[len(mod) + 1:]) if spec != mod else moddir
                if target in self.dirs or target == "":
                    return [target], None
                return [], None
        return [], spec

    def _py_path(self, base: str, segs: list[str]):
        if not segs:
            return base if (base == "" or base in self.dirs) else None
        stem = pj(base, *segs)
        if stem + ".py" in self.all_files:
            return stem + ".py"
        if pj(stem, "__init__.py") in self.all_files or stem in self.dirs:
            return stem
        return None

    def _resolve_python(self, src, spec):
        level, mod, names = spec["level"], spec["module"], spec["names"]
        parts = mod.split(".") if mod else []
        if level:
            base = parent_dir(src)
            for _ in range(level - 1):
                base = parent_dir(base)
            roots = [base]
        else:
            roots = self.py_roots
        for root in roots:
            targets = []
            for name in (names or [None]):
                segs = parts + ([name] if name and name != "*" else [])
                hit = self._py_path(root, segs)
                if hit is None and len(segs) > 1:
                    hit = self._py_path(root, segs[:-1])
                if hit is not None and hit not in targets:
                    targets.append(hit)
            if targets:
                return targets, None
        if level:
            # from .. import name：name 定义在上级包 __init__ 里时，依赖指向该包目录
            base = roots[0]
            return ([base] if base and base in self.dirs else []), None
        return [], parts[0] if parts else None

    def _ts_file(self, cand: str | None):
        if cand is None:
            return None
        if cand in self.all_files:
            return cand
        stem, ext = posixpath.splitext(cand)
        for alt in JS_TO_TS.get(ext, []):
            if stem + alt in self.all_files:
                return stem + alt
        for ext2 in TS_EXTS:
            if cand + ext2 in self.all_files:
                return cand + ext2
        for ext2 in TS_EXTS:
            if pj(cand, "index" + ext2) in self.all_files:
                return pj(cand, "index" + ext2)
        if cand in self.dirs:
            return cand
        return None

    def _resolve_ts(self, src, spec):
        if spec.startswith("."):
            hit = self._ts_file(norm_rel(pj(parent_dir(src), spec)))
            return ([hit] if hit else []), None
        for cdir, aliases in self.ts_configs:
            if cdir and not (src == cdir or src.startswith(cdir + "/")):
                continue
            matched = False
            for prefix, dirs, wildcard in aliases:
                if (wildcard and spec.startswith(prefix)) or (not wildcard and spec == prefix):
                    matched = True
                    rest = spec[len(prefix):] if wildcard else ""
                    for d in dirs:
                        hit = self._ts_file(norm_rel(pj(d, rest)))
                        if hit:
                            return [hit], None
            if matched:
                return [], None
        if spec.startswith("@"):
            return [], "/".join(spec.split("/")[:2])
        return [], spec.split("/")[0]

    def _resolve_java(self, src, spec):
        names = spec.split(".")
        # 只允许去掉类名、嵌套类 / 静态成员这两段，避免外部 jar 连到很短的根包
        for k in range(len(names), max(len(names) - 3, 0), -1):
            prefix = ".".join(names[:k])
            if prefix in self.java_packages:
                return sorted(self.java_packages[prefix]), None
        return [], spec

    _resolve_kotlin = _resolve_java

    def _resolve_dart(self, src, spec):
        if spec.startswith("dart:"):
            return [], spec
        if spec.startswith("package:"):
            name, _, rest = spec[len("package:"):].partition("/")
            if name in self.dart_packages:
                target = pj(self.dart_packages[name], rest)
                return ([target] if target in self.all_files else []), None
            return [], f"package:{name}"
        target = norm_rel(pj(parent_dir(src), spec))
        return ([target] if target in self.all_files else []), None

    def _resolve_swift(self, src, spec):
        dirs = self.swift_modules.get(spec)
        if dirs and len(dirs) == 1:
            return dirs, None
        return [], spec

    def _resolve_c(self, src, spec):
        kind, path = spec
        if kind == '"':
            local = norm_rel(pj(parent_dir(src), path))
            if local in self.all_files:
                return [local], None
        hits = self.suffix_index.get(path.lstrip("./"), set())
        if not is_test_path(src):
            hits = {h for h in hits if not is_test_path(h)}   # 生产代码不连到测试桩
        if len(hits) == 1:
            return [next(iter(hits))], None
        return [], path


EXTRACTORS = {
    "go": go_imports,
    "python": py_imports,
    "ts": ts_imports,
    "java": java_imports,
    "kotlin": java_imports,
    "dart": simple_imports(DART_IMPORT),
    "swift": simple_imports(SWIFT_IMPORT),
    "c": c_includes,
}


# ---------------------------------------------------------------- SQL 表引用（M13）

SQL_TABLE = re.compile(r"""\b(?:FROM|JOIN|INTO|UPDATE)\s+[`"\[]?([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)""")
SQL_KEYWORDS = ("FROM", "JOIN", "INTO", "UPDATE")
SQL_NOT_TABLE = {"DUAL", "SELECT", "WHERE", "SET", "VALUES", "LATERAL", "UNNEST"}
ORM_TABLE = [
    re.compile(r"""@Table\(\s*name\s*=\s*["'](\w+)["']"""),
    re.compile(r"""__tablename__\s*=\s*["'](\w+)["']"""),
    re.compile(r"""db_table\s*=\s*["'](\w+)["']"""),
    re.compile(r"""@Entity\(\s*(?:\{\s*name\s*:\s*)?["'](\w+)["']"""),
]
ORM_HINTS = ("@Table", "__tablename__", "db_table", "@Entity")
GO_TABLENAME = re.compile(r"\)\s*TableName\(\)\s*string")
GO_RETURN_STR = re.compile(r'return\s+"(\w+)"')


def sql_tables(text: str):
    out = []
    lines = text.splitlines()
    in_backtick = in_triple = False
    for i, line in enumerate(lines, 1):
        raw_at_start = in_backtick or in_triple
        if line.count("`") % 2:
            in_backtick = not in_backtick
        if (line.count('"""') + line.count("'''")) % 2:
            in_triple = not in_triple
        in_string = raw_at_start or in_backtick or in_triple or any(q in line for q in ('"', "'", "`"))
        if in_string and any(k in line for k in SQL_KEYWORDS):
            for t in SQL_TABLE.findall(line):
                if t.upper() not in SQL_NOT_TABLE:
                    out.append((i, t, "sql"))
        if any(h in line for h in ORM_HINTS):
            for rx in ORM_TABLE:
                for t in rx.findall(line):
                    out.append((i, t, "orm"))
        if "TableName" in line and GO_TABLENAME.search(line):
            for j in range(i - 1, min(i + 3, len(lines))):
                m = GO_RETURN_STR.search(lines[j])
                if m:
                    out.append((j + 1, m.group(1), "orm"))
                    break
    return out


# ---------------------------------------------------------------- 外部进程：git 与 jscpd

def _git_env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"      # git ≥ 2.44：partial clone 不按需联网拉对象
    return env


def _git_out(repo: str, args: list[str], timeout: int = 60) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", repo, "-c", "core.fsmonitor=false", *args], capture_output=True,
                              env=_git_env(), timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else None


def parse_git_log(out: str) -> list[dict]:
    """记录之间用 NUL 分隔（git 不允许提交说明里有 NUL），标题里夹带的分隔符伪造不出新提交；
    sha 不是十六进制的整条丢掉——AI 复核时会拿 sha 去执行 git show。"""
    commits = []
    for rec in out.split("\x00"):
        rec = rec.strip("\n")
        if not LOG_HEADER.match(rec):
            continue
        header, *rest = rec.split("\n")
        sha, date, subject = header.split("\x1f", 2)
        files = [line.strip() for line in rest if line.strip()]
        commits.append({"sha": sha, "date": date, "subject": CONTROL_CHARS.sub(" ", subject)[:MAX_SUBJECT_CHARS],
                        "files": files})
    return commits


def git_log(repo: str, since: str | None) -> tuple[list[dict], str | None]:
    """近期提交，路径相对 repo（可以是大仓的子目录）；不做重命名检测，partial clone 不会按需联网拉 blob。"""
    if not shutil.which("git"):
        return [], f"git not found; {'/'.join(GIT_IDS)} skipped"
    cmd = ["git", "-C", repo, "-c", "core.quotepath=off", "-c", "core.fsmonitor=false", "log", "--no-merges",
           "--no-renames", "--relative",
           "--format=%x00%H%x1f%aI%x1f%s", "--name-only"]
    if since:
        cmd.append(f"--since={since}")
    cmd += ["--", "."]
    try:
        proc = subprocess.run(cmd, capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return [], f"git log 超过 {GIT_TIMEOUT}s；{'/'.join(GIT_IDS)} 本期未查"
    if proc.returncode != 0:
        return [], (proc.stderr.decode("utf-8", "replace").strip() or "git log failed")[:300]
    commits = parse_git_log(proc.stdout.decode("utf-8", "surrogateescape"))
    if commits:
        return commits, None
    latest = _git_out(repo, ["log", "-1", "--format=%aI", "--", "."]) or "无"
    return [], f"git 统计窗口（since={since or '全部'}）内没有提交，最新提交 {latest}；{'/'.join(GIT_IDS)} 本期无数据"


def git_head(repo: str) -> str | None:
    if not shutil.which("git"):
        return None
    return _git_out(repo, ["rev-parse", "HEAD"]) or None


def _run_isolated(cmd: list[str], cwd: str, env: dict, timeout: int):
    """在新的进程组里运行；超时整组杀掉，避免孙进程泄漏。返回 (退出码, stderr 尾部, 是否超时)。"""
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return None, str(exc), False
    try:
        _, err = proc.communicate(timeout=timeout)
        return proc.returncode, err.decode("utf-8", "replace")[-500:], False
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=10)
        return None, "", True


def run_jscpd(repo: str, min_lines: int, min_tokens: int, timeout: int):
    """在空的临时目录里运行锁定版本的 jscpd，被扫仓以绝对路径传入。

    npm / npx 会从 cwd 读取 .npmrc、node_modules/.bin，jscpd 会从 cwd 读取 .jscpd.json / package.json；
    以被扫仓为 cwd 等于让被扫仓决定扫描机执行什么。这里 cwd 与配置都不来自被扫仓。
    """
    npx = shutil.which("npx")
    if not npx:
        return None, "npx not found; M17 skipped"
    repo_abs = os.path.abspath(repo)
    env = {k: v for k, v in os.environ.items() if not k.lower().startswith("npm_config_")}
    env["npm_config_ignore_scripts"] = "true"
    ignore = ",".join(
        [f"**/{d}/**" for d in sorted(SKIP_DIRS)]
        + ["**/test/**", "**/tests/**", "**/__tests__/**", "**/*_test.*", "**/*_unittest.*", "**/*.spec.*", "**/*.test.*"]
        + [f"**/{g}" for g in GENERATED_PATTERNS]
    )
    with tempfile.TemporaryDirectory(prefix="arch-scan-jscpd-") as tmp:
        cfg = os.path.join(tmp, "jscpd.json")
        for name in ("jscpd.json", "package.json"):   # package.json 让 npx 不再向上级目录找 node_modules
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write("{}")
        out = os.path.join(tmp, "report")
        cmd = [npx, "-y", f"jscpd@{JSCPD_VERSION}", "--config", cfg, "--noSymlinks", "--absolute",
               "--min-lines", str(min_lines), "--min-tokens", str(min_tokens), "--format", JSCPD_FORMATS,
               "--reporters", "json", "--output", out, "--silent", "--ignore", ignore, repo_abs]
        rc, _, timed_out = _run_isolated(cmd, tmp, env, timeout)
        if timed_out:
            return None, "jscpd timeout; M17 skipped"
        report = os.path.join(out, "jscpd-report.json")
        if not os.path.exists(report):
            return None, f"jscpd produced no report (exit {rc}); M17 skipped"
        try:
            with open(report, encoding="utf-8") as fh:
                dups = json.load(fh)["duplicates"]
            clones = []
            for d in dups:
                a, b = d["firstFile"], d["secondFile"]
                clones.append({"a": _rel_to(a["name"], repo_abs, tmp), "a_start": a.get("start"),
                               "b": _rel_to(b["name"], repo_abs, tmp), "b_start": b.get("start"),
                               "lines": int(d.get("lines", 0))})
        except (ValueError, KeyError, TypeError):
            return None, "jscpd report unreadable; M17 skipped"
    return clones, None


def _rel_to(name: str, repo_abs: str, cwd: str | None = None) -> str:
    """jscpd 报告里的路径 → 仓内相对路径（兼容绝对路径与相对 jscpd 工作目录的路径）。"""
    if not os.path.isabs(name) and cwd:
        name = os.path.join(cwd, name)
    if os.path.isabs(name):
        name = os.path.relpath(os.path.realpath(name), os.path.realpath(repo_abs))
    name = name.replace(os.sep, "/")
    return name[2:] if name.startswith("./") else name


# ---------------------------------------------------------------- collect

def list_files(root: str, use_git: bool) -> tuple[set[str], list[str]]:
    """列出仓内普通文件（git 仓用 ls-files，遵守 .gitignore）；跳过符号链接、非普通文件与超大代码文件。"""
    rels = None
    if use_git and shutil.which("git"):
        try:
            proc = subprocess.run(["git", "-C", root, "-c", "core.fsmonitor=false", "ls-files", "-z", "--cached",
                                   "--others", "--exclude-standard"],
                                  capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT)
            if proc.returncode == 0:
                rels = [p.decode("utf-8", "surrogateescape") for p in proc.stdout.split(b"\0") if p]
        except (subprocess.TimeoutExpired, OSError):
            rels = None
    if rels is None:
        rels = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            rel_dir = os.path.relpath(dirpath, root)
            rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            rels.extend(pj(rel_dir, fn) for fn in filenames)
    all_files, oversized = set(), []
    for rel in rels:
        parts = rel.split("/")
        if any(p in SKIP_DIRS or p.startswith(".") for p in parts[:-1]):
            continue
        try:
            st = os.lstat(os.path.join(root, rel))
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_size > MAX_FILE_BYTES:
            if os.path.splitext(rel)[1].lower() in LANG_BY_EXT:
                oversized.append(rel)
            continue
        all_files.add(rel)
    return all_files, oversized


def draft_modules(files: dict[str, dict], index: RepoIndex):
    units: dict[str, int] = defaultdict(int)
    java_roots = ("src/main/java/", "src/main/kotlin/")
    pkg_dirs = [parent_dir(f) for f, m in files.items() if m["lang"] in ("java", "kotlin") and not m["test"]]
    java_common = "/".join(os.path.commonprefix([d.split("/") for d in pkg_dirs])) if pkg_dirs else None
    for path, meta in files.items():
        if meta["test"] or meta["generated"]:
            continue
        d = parent_dir(path)
        if not d:
            key = "."
        elif meta["lang"] in ("java", "kotlin") and java_common is not None and any(r in f"{d}/" for r in java_roots):
            rest = d[len(java_common):].strip("/")
            key = pj(java_common, rest.split("/")[0]) if rest else java_common
        else:
            parts = d.split("/")
            key_parts = []
            i = 0
            while i < len(parts) and parts[i] in CONTAINER_DIRS and len(key_parts) < 2:
                key_parts.append(parts[i])
                i += 1
            if i < len(parts):
                key_parts.append(parts[i])
            key = "/".join(key_parts)
        units[key] += meta["nloc"]
    mods = []
    for key, nloc in sorted(units.items(), key=lambda kv: (-kv[1], kv[0])):
        name = "root" if key == "." else key.rsplit("/", 1)[-1]
        mods.append({"name": name, "paths": [key], "nloc": nloc})
    seen = Counter(m["name"] for m in mods)
    for m in mods:
        if seen[m["name"]] > 1:
            m["name"] = m["paths"][0].replace("/", ".")
    build_units = sorted({parent_dir(f) or "." for f in index.all_files if f.rsplit("/", 1)[-1] in BUILD_FILES})
    return mods, build_units


def collect(repo: str, since: str | None = "6 months ago", use_git: bool = True,
            use_jscpd: bool = True, jscpd_timeout: int = 600) -> dict:
    root = os.path.abspath(repo)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"仓库路径不存在或不是目录：{root}")
    notes: list[str] = []
    all_files, oversized = list_files(root, use_git)
    if oversized:
        notes.append(f"跳过 {len(oversized)} 个超过 2MB 的代码文件（按生成物处理），如 {', '.join(sorted(oversized)[:3])}")
    files: dict[str, dict] = {}
    texts: dict[str, str] = {}
    for f in sorted(all_files):
        lang = LANG_BY_EXT.get(os.path.splitext(f)[1].lower())
        if not lang:
            continue
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        files[f] = {"lang": lang, "nloc": count_nloc(text, lang), "lines": count_lines(text),
                    "test": is_test_path(f), "generated": is_generated(f)}
        # 超长行不做正则解析，保留行号
        texts[f] = "\n".join(line if len(line) <= MAX_LINE_CHARS else "" for line in text.splitlines())
    index = RepoIndex(root, files, texts, all_files, notes)

    edges, tables = [], []
    external: dict[str, dict] = {}
    for f, meta in files.items():
        if meta["generated"]:
            continue
        text = texts[f]
        for line, spec in EXTRACTORS[meta["lang"]](text):
            targets, ext_name = index.resolve(f, meta["lang"], spec)
            for t in targets:
                if t != f and t != parent_dir(f):
                    edges.append({"src": f, "dst": t, "line": line})
            if ext_name:
                e = external.setdefault(ext_name, {"count": 0, "sites": []})
                e["count"] += 1
                if len(e["sites"]) < 200:
                    e["sites"].append({"src": f, "line": line})
        if not meta["test"] and "migration" not in f.lower():
            for line, table, kind in sql_tables(text):
                tables.append({"src": f, "line": line, "table": table, "kind": kind})

    commits, git_note = (git_log(root, since) if use_git else ([], f"git disabled; {'/'.join(GIT_IDS)} skipped"))
    clones, jscpd_note = (run_jscpd(root, 10, 70, jscpd_timeout) if use_jscpd else (None, "jscpd disabled; M17 skipped"))
    drafts, build_units = draft_modules(files, index)
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": root,
        "head": git_head(root) if use_git else None,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "since": since,
        "notes": notes + [n for n in (git_note, jscpd_note) if n],
        "files": files,
        "edges": edges,
        "external_imports": external,
        "sql_tables": tables,
        "commits": commits,
        "clones": clones,
        "draft_modules": drafts,
        "build_units": build_units,
    }


# ---------------------------------------------------------------- 忽略名单

def _strip_yaml_comment(line: str) -> str:
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _scalar(v: str):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        return [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def parse_ignore_minimal(text: str) -> list[dict]:
    """没有 PyYAML 时的简易解析：支持「- key: value」「key: value」「[a, b]」与块状列表。"""
    entries: list[dict] = []
    cur: dict | None = None
    list_key: str | None = None

    def put(key: str, value: str):
        value = value.strip()
        if value == "":
            cur[key] = []
            return key
        cur[key] = _scalar(value)
        return None

    for raw in text.splitlines():
        line = _strip_yaml_comment(raw).rstrip()
        if not line.strip():
            continue
        m = re.match(r"^-\s+(\w+):\s*(.*)$", line)
        if m:
            cur = {}
            entries.append(cur)
            list_key = put(m.group(1), m.group(2))
            continue
        m = re.match(r"^\s+-\s+(.*)$", line)
        if m and cur is not None and list_key:
            cur[list_key].append(_scalar(m.group(1)))
            continue
        m = re.match(r"^\s+(\w+):\s*(.*)$", line)
        if m and cur is not None:
            list_key = put(m.group(1), m.group(2))
    return entries


def load_ignore(path: str | None) -> list:
    """读 .arch-ignore.yaml（有 PyYAML 用 safe_load，否则用简易解析）。文件过大直接拒绝。"""
    if not path or not os.path.exists(path):
        return []
    if os.path.getsize(path) > MAX_IGNORE_BYTES:
        raise ValueError(f"忽略名单超过 {MAX_IGNORE_BYTES} 字节：{path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse_ignore_minimal(text)
    data = yaml.safe_load(text) or []
    return data if isinstance(data, list) else []


def _normalize_ignore(entry) -> dict | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not str(entry.get("reason", "")).strip():
        return None
    mods = entry.get("modules")
    mods = [mods] if isinstance(mods, str) else mods
    if not isinstance(mods, list) or not all(isinstance(m, str) for m in mods):
        return None
    key = entry.get("key", None)
    if key is not None and not isinstance(key, str):
        return None
    if entry["id"] in FILE_IDS and key is not None and not key.strip():   # 文件级写了空路径：挡不住任何文件
        return None
    return {"id": entry["id"], "modules": sorted(mods), "key": key}


# ---------------------------------------------------------------- analyze

def tarjan_scc(nodes, adj):
    index, low, on, stack, sccs = {}, {}, set(), [], []
    counter = 0
    for start in nodes:
        if start in index:
            continue
        work = [(start, iter(sorted(adj.get(start, ()))))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on.add(start)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(adj.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    sccs.append(sorted(comp))
    return sccs


class ModuleMap:
    def __init__(self, spec: dict):
        self.spec = spec
        self.warnings: list[str] = []
        self.layers = [x for x in (spec.get("layers") or []) if isinstance(x, str)]
        self.modules: dict[str, dict] = {}
        self.renames: dict[str, str] = {}
        pairs = []
        for m in spec.get("modules") or []:
            if not isinstance(m, dict) or not isinstance(m.get("name"), str):
                self.warnings.append(f"模块条目缺少 name，已忽略：{short_repr(m)}")
                continue
            name = m["name"]
            m = dict(m)
            paths = m.get("paths")
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                self.warnings.append(f"{name}：paths 必须是字符串列表（不是 path），该模块不会匹配任何文件")
                paths = []
            role = m.get("role")
            if role is not None and (not isinstance(role, str) or role not in ROLES):
                self.warnings.append(f"{name}：未知 role {short_repr(role)}，可选 {sorted(ROLES)}，已忽略")
                m.pop("role")
            layer = m.get("layer")
            if layer is not None and (not isinstance(layer, str) or layer not in self.layers):
                self.warnings.append(f"{name}：layer {short_repr(layer)} 不在 layers 里，M01 对它不生效")
                m.pop("layer")
            olds = m.get("renamed_from") or []
            for old in [olds] if isinstance(olds, str) else olds:
                if isinstance(old, str):
                    self.renames[old] = name
            self.modules[name] = dict(m, paths=paths)
            pairs.extend((name, p) for p in paths)
        self.sorted_paths = sorted(pairs, key=lambda x: (x[1] in ("", "."), -len(x[1]), x[1]))
        def str_list(x):
            return isinstance(x, list) and all(isinstance(i, str) for i in x)

        self.allow = {tuple(x) for x in spec.get("allow") or [] if str_list(x) and len(x) == 2}
        self.isolated = [set(g) for g in spec.get("isolated") or [] if str_list(g)]
        self._cache: dict[str, str | None] = {}

    def assign(self, path: str) -> str | None:
        if path in self._cache:
            return self._cache[path]
        found = None
        for name, p in self.sorted_paths:
            if path_matches(path, p):
                excl = self.modules[name].get("exclude") or []
                if any(isinstance(e, str) and path_matches(path, e) for e in excl):
                    continue
                found = name
                break
        self._cache[path] = found
        return found

    def role(self, name):
        return (self.modules.get(name) or {}).get("role")

    def layer_index(self, name):
        layer = (self.modules.get(name) or {}).get("layer")
        return self.layers.index(layer) if layer in self.layers else None

    def public_ok(self, name, dst_path):
        pub = (self.modules.get(name) or {}).get("public")
        if not pub:
            return True
        return any(isinstance(p, str) and path_matches(dst_path, p) for p in pub)

    def rename_fp(self, fp: str) -> str:
        parts = fp.split("|")
        if len(parts) >= 2 and self.renames:
            parts[1] = "+".join(sorted(self.renames.get(x, x) for x in parts[1].split("+") if x))
        return "|".join(parts)


def fingerprint(c: dict) -> str:
    mods = "" if c["id"] in FILE_IDS else "+".join(sorted(c["modules"]))
    return "|".join([c["id"], mods, c.get("key", "")])


class Candidates:
    """候选集合：按指纹去重，证据最多 5 条。"""

    def __init__(self):
        self.items: dict[str, dict] = {}

    def add(self, cid, modules, detail, evidence, key=""):
        c = {"id": cid, "modules": sorted(set(modules)), "detail": detail}
        if key:
            c["key"] = key
        c["fingerprint"] = fingerprint(c)
        ev = [e for e in evidence if e]
        old = self.items.get(c["fingerprint"])
        if old:
            old["evidence"] = (old["evidence"] + [e for e in ev if e not in old["evidence"]])[:5]
            return
        c["evidence"] = ev[:5]
        self.items[c["fingerprint"]] = c

    def values(self):
        return list(self.items.values())


def _merged_len(regions) -> int:
    """同一文件的重复区间合并后的总行数。"""
    by_file = defaultdict(list)
    for f, start, n in regions:
        s = start or 1
        by_file[f].append((s, s + max(n, 0)))
    total = 0
    for spans in by_file.values():
        spans.sort()
        cur_s, cur_e = spans[0]
        for s, e in spans[1:]:
            if s <= cur_e:
                cur_e = max(cur_e, e)
            else:
                total += cur_e - cur_s
                cur_s, cur_e = s, e
        total += cur_e - cur_s
    return total


def analyze(raw: dict, map_spec: dict | None = None, ignore: list | None = None,
            previous: dict | None = None, thresholds: dict | None = None) -> dict:
    if map_spec is None:
        map_spec = {"modules": [{"name": m["name"], "paths": m["paths"]} for m in raw.get("draft_modules", [])]}
    if not isinstance(map_spec, dict):
        raise ValueError("module-map 顶层必须是 JSON 对象")
    notes = list(raw.get("notes", []))
    th = dict(DEFAULT_THRESHOLDS)
    map_th = map_spec.get("thresholds") or {}
    for k, v in (map_th.items() if isinstance(map_th, dict) else []):
        if k in th and isinstance(v, (int, float)) and not isinstance(v, bool):
            th[k] = type(th[k])(v)
        else:
            notes.append(f"module-map 里的阈值 {k!r} 无效，已忽略")
    th.update(thresholds or {})
    mm = ModuleMap(map_spec)
    files = raw["files"]

    def code_file(f):
        meta = files.get(f)
        if meta is not None:
            return not meta["test"] and not meta["generated"]
        ext = os.path.splitext(f)[1].lower()
        return ext in LANG_BY_EXT and not is_test_path(f) and not is_generated(f)

    nloc, lines = Counter(), Counter()
    unmapped = []
    for f, meta in files.items():
        if meta["test"] or meta["generated"]:
            continue
        m = mm.assign(f)
        if m is None:
            unmapped.append(f)
        else:
            nloc[m] += meta["nloc"]
            lines[m] += meta.get("lines", meta["nloc"])
    total = sum(nloc.values()) or 1
    names = sorted(mm.modules)

    medges: dict[tuple, list] = defaultdict(list)
    for e in raw["edges"]:
        if not code_file(e["src"]) or is_test_path(e["dst"]):
            continue
        ms, md = mm.assign(e["src"]), mm.assign(e["dst"])
        if ms and md and ms != md:
            medges[(ms, md)].append(e)
    adj = defaultdict(set)
    for (s, d) in medges:
        adj[s].add(d)
    ce = {n: len(adj.get(n, ())) for n in names}
    ca = Counter(d for (_, d) in medges)
    inst = {n: (ce[n] / (ca[n] + ce[n]) if (ca[n] + ce[n]) else 0.0) for n in names}

    def ev(edge_list, limit=5):
        return [f'{e["src"]}:{e["line"]} -> {e["dst"]}' for e in edge_list[:limit]]

    cands = Candidates()
    add = cands.add

    # A 组：对照正本
    violations: set[tuple] = set()
    for (s, d), el in sorted(medges.items()):
        if (s, d) in mm.allow:
            continue
        ls, ld = mm.layer_index(s), mm.layer_index(d)
        if ls is not None and ld is not None and ls > ld:
            violations.add((s, d))
            add("M01", [s, d], f"下层 {s}（{mm.modules[s].get('layer')}）依赖上层 {d}（{mm.modules[d].get('layer')}）", ev(el))
        bad = [e for e in el if not mm.public_ok(d, e["dst"])]
        if bad:
            violations.add((s, d))
            add("M02", [s, d], f"{s} 绕过 {d} 的公开接口，直接依赖其内部实现", ev(bad))
        elif any(s in g and d in g for g in mm.isolated):
            violations.add((s, d))
            add("M02", [s, d], f"同层隔离模块 {s} 直接依赖 {d}", ev(el))
        rs, rd = mm.role(s), mm.role(d)
        if rs == "base" and rd not in ("base", "leaf"):
            violations.add((s, d))
            add("M04", [s, d], f"底座 {s} 依赖非底座模块 {d}（role={rd or '未标注'}）", ev(el))
        elif rs == "leaf":
            violations.add((s, d))
            add("M04", [s, d], f"{s} 标为 leaf（规范要求不依赖任何层），却依赖 {d}", ev(el))

    libs = [r for r in map_spec.get("controlled_libs") or [] if isinstance(r, dict)]
    if not libs:
        notes.append("M03 本期未查：module-map 未提供 controlled_libs")
    for rule in libs:
        lib, only_in = rule.get("lib", ""), set(rule.get("only_in") or [])
        if not isinstance(lib, str) or not lib:
            continue
        stem = lib.rstrip("/.")
        by_mod = defaultdict(list)
        for ext_name, info in raw.get("external_imports", {}).items():
            if ext_name == stem or ext_name.startswith(stem + "/") or ext_name.startswith(stem + "."):
                for site in info["sites"]:
                    m = mm.assign(site["src"])
                    if m and m not in only_in and code_file(site["src"]):
                        by_mod[m].append(f'{site["src"]}:{site["line"]} ({ext_name})')
        for m, sites in sorted(by_mod.items()):
            add("M03", [m], f"受控依赖 {lib} 只允许在 {sorted(only_in)} 使用，{m} 直接引用 {len(sites)} 处", sites, key=lib)

    table_use = defaultdict(lambda: defaultdict(list))
    for t in raw.get("sql_tables", []):
        m = mm.assign(t["src"])
        if m:
            table_use[t["table"]][m].append(f'{t["src"]}:{t["line"]}')
    for table, by_mod in sorted(table_use.items()):
        if len(by_mod) >= 2:
            evid = [f"{m}: {', '.join(v[:2])}" for m, v in sorted(by_mod.items())]
            add("M13", list(by_mod), f"表 {table} 被 {len(by_mod)} 个模块直接读写", evid, key=table)

    pnames = [p for p in map_spec.get("product_names") or [] if isinstance(p, str) and p]
    if not pnames:
        notes.append("M05 本期未查：module-map 未提供 product_names")
    else:
        rx = re.compile(r"\b(" + "|".join(re.escape(p) for p in pnames) + r")\b", re.I)
        hits_by_mod: dict[str, list] = {}
        unreadable = 0
        for f, meta in sorted(files.items()):
            m = mm.assign(f)
            if not m or meta["test"] or meta["generated"] or mm.role(m) in ("product", "root"):
                continue
            try:
                with open(os.path.join(raw["repo"], f), encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if len(line) > MAX_LINE_CHARS:
                            continue
                        hit = rx.search(line)
                        if hit:
                            hits_by_mod.setdefault(m, []).append(f"{f}:{i}: {hit.group(1)}")   # 不带源码原文
            except OSError:
                unreadable += 1
        if unreadable:
            notes.append(f"M05：{unreadable} 个文件读取失败（仓库路径可能已变），结果不完整")
        for m, hits in sorted(hits_by_mod.items()):
            add("M05", [m], f"{m} 中出现产品 / 品牌名 {len(hits)} 处（待 AI 判断是否为业务分支）", hits)

    # B 组：结构
    sccs = tarjan_scc(names, adj)
    for comp in sccs:
        inner = sorted((s, d) for (s, d) in medges if s in comp and d in comp)
        add("M06", comp, f"{len(comp)} 个模块成环", [f"{s} -> {d}: {ev(medges[(s, d)], 1)[0]}" for s, d in inner])
    for (s, d), el in sorted(medges.items()):
        if inst[d] - inst[s] >= th["unstable_delta"] - 1e-9:
            add("M07", [s, d], f"{s}（I={inst[s]:.2f}）依赖更不稳定的 {d}（I={inst[d]:.2f}）", ev(el, 2))
    for n in names:
        if ca[n] >= th["hub_in"] and ce[n] >= th["hub_out"]:
            add("M08", [n], f"{n} 入度 {ca[n]}、出度 {ce[n]}", [f"依赖 {sorted(adj[n])}"])
    if len([n for n in names if nloc[n]]) >= th["god_min_modules"]:
        for n in names:
            if nloc[n] / total > th["god_share"]:
                add("M09", [n], f"{n} 占已归入模块有效代码的 {nloc[n] / total:.0%}", [f"{nloc[n]} / {total} 行"])
    for n in names:
        spec_m = mm.modules[n]
        base = ((spec_m.get("paths") or [n])[0] or n).rsplit("/", 1)[-1].lower()
        if not spec_m.get("canonical") and (n.lower() in VAGUE_NAMES or base in VAGUE_NAMES):
            add("M14", [n], f"模块名 {n} 属于空洞名，待 AI 判断名实是否相符", [f"paths={spec_m.get('paths')}"])

    regions = defaultdict(set)
    pair_clones = defaultdict(set)
    clone_ev = defaultdict(list)
    clones = raw.get("clones") or []
    for idx, c in enumerate(clones):
        ma, mb = mm.assign(c["a"]), mm.assign(c["b"])
        if not ma or not mb or is_test_path(c["a"]) or is_test_path(c["b"]):
            continue
        site = f'{c["a"]}:{c["a_start"]} <-> {c["b"]}:{c["b_start"]}（{c["lines"]} 行）'
        regions[ma].add((c["a"], c["a_start"], c["lines"]))
        regions[mb].add((c["b"], c["b_start"], c["lines"]))
        clone_ev[ma].append(site)
        if ma != mb:
            clone_ev[mb].append(site)
            key = tuple(sorted((ma, mb)))
            pair_clones[key].add(idx)
            clone_ev[key].append(site)
    dup = {n: _merged_len(r) for n, r in regions.items()}
    for n in names:
        if lines[n] and dup.get(n, 0) / lines[n] >= th["dup_ratio"]:
            ratio = min(dup[n] / lines[n], 1.0)
            add("M17", [n], f"{n} 重复率 {ratio:.1%}（{dup[n]} / {lines[n]} 行）", clone_ev[n])
    for key, ids in sorted(pair_clones.items()):
        plines = sum(clones[i]["lines"] for i in ids)
        if plines >= th["dup_pair_lines"]:
            add("M17", list(key), f"{key[0]} 与 {key[1]} 之间重复 {plines} 行", clone_ev[key])

    # C 组：演化
    churn, fixes = Counter(), Counter()
    fchurn, ffixes = Counter(), Counter()   # 文件级（M18 / M19）
    pair = Counter()
    fix_ev = defaultdict(list)
    ffix_ev = defaultdict(list)
    feat_spread = []
    used = used_fix = 0
    for cm in raw.get("commits", []):
        if len(cm["files"]) > th["bulk_commit_files"]:
            continue
        mods = {mm.assign(f) for f in cm["files"] if code_file(f)}
        mods.discard(None)
        if not mods:
            continue
        subject = cm["subject"][:MAX_SUBJECT_CHARS]
        is_fix = bool(FIX_RE.match(subject))
        fix_line = f"{cm['sha'][:10]} {subject[:80]}"
        used += 1
        used_fix += is_fix
        for f in cm["files"]:   # 只算现存、已归入模块的代码文件；已删除的文件不再有意义
            if f in files and code_file(f) and mm.assign(f):
                fchurn[f] += 1
                if is_fix:
                    ffixes[f] += 1
                    if len(ffix_ev[f]) < 5:   # 证据只用最近 5 次（git log 新的在前）
                        ffix_ev[f].append(fix_line)
        for m in mods:
            churn[m] += 1
            if is_fix:
                fixes[m] += 1
                fix_ev[m].append(fix_line)
        for a, b in combinations(sorted(mods), 2):
            pair[(a, b)] += 1
        if FEAT_RE.match(subject):
            feat_spread.append(len(mods))
    repo_fix = used_fix / used if used else 0.0
    for (a, b), n in sorted(pair.items()):
        coupling = n / min(churn[a], churn[b])
        if n >= th["cochange_min"] and coupling >= th["coupling_min"]:
            implicit = (a, b) not in medges and (b, a) not in medges
            add("M11", [a, b], f"{a} ↔ {b} 共同修改 {n} 次，耦合度 {coupling:.0%}" + ("（无依赖关系：隐性耦合）" if implicit else ""),
                [f"churn {a}={churn[a]} {b}={churn[b]}"])
    for n in names:
        if not churn[n] or not fixes[n]:
            continue
        ratio = fixes[n] / churn[n]
        if fixes[n] >= th["fix_min"] and ratio >= th["fix_ratio"] and ratio >= repo_fix + th["fix_margin"]:
            add("M12", [n], f"{n} 近期 {churn[n]} 次提交中 {fixes[n]} 次是 fix（{ratio:.0%}，全仓基线 {repo_fix:.0%}）",
                fix_ev[n])

    # 文件级：超长文件（M18）、文件缺陷热点（M19）；两者叠在同一个文件上就是热点文件
    has_git = used > 0   # 有提交但全是批量提交 / 没改到代码文件，也算没有可用数据
    if raw.get("commits") and not has_git:
        notes.append(f"窗口内的提交都是批量提交或没改到代码文件；{'/'.join(GIT_IDS)} 本期无数据")
    hot_files = []
    for f, meta in sorted(files.items()):
        if meta["test"] or meta["generated"]:
            continue
        m = mm.assign(f)
        if m is None:
            continue
        long_file = meta["nloc"] >= th["long_file_nloc"]
        fix_hot = ffixes[f] > 0 and ffixes[f] >= th["file_fix_min"]
        if not (long_file or fix_hot):
            continue
        history = f"近期 {fchurn[f]} 次提交，其中 fix {ffixes[f]} 次" if has_git else "没有可用的 git 提交"
        if long_file:
            add("M18", [m], f"{f} 有 {meta['nloc']} 行有效代码（{history}）",
                [f"{f}：{meta['nloc']} 行有效代码 / {meta.get('lines', meta['nloc'])} 物理行"], key=f)
        if fix_hot:
            add("M19", [m], f"{f} 近期 {fchurn[f]} 次提交中 {ffixes[f]} 次是 fix"
                f"（{ffixes[f] / fchurn[f]:.0%}，全仓基线 {repo_fix:.0%}）", ffix_ev[f], key=f)
        hot_files.append({"path": f, "module": m, "nloc": meta["nloc"], "churn": fchurn[f], "fixes": ffixes[f],
                          "long": long_file, "fix_hot": fix_hot})

    # 本期没查的信号：它们的旧发现不能算「已修」
    unchecked: set[str] = set()
    if not has_git:
        unchecked |= set(GIT_IDS)
    if raw.get("clones") is None:
        unchecked.add("M17")
    if not libs:
        unchecked.add("M03")
    if not pnames:
        unchecked.add("M05")
    if not files:
        unchecked |= SCRIPT_IDS

    # 忽略名单与上期对比
    valid_ignore, invalid_ignore = [], []
    for entry in ignore or []:
        norm = _normalize_ignore(entry)
        if norm:
            valid_ignore.append(norm)
        else:
            invalid_ignore.append(short_repr(entry))

    for e in valid_ignore:   # 忽略名单里的旧模块名按 renamed_from 映射
        e["modules"] = sorted(mm.renames.get(m, m) for m in e["modules"])

    def ignored(c):
        def hit(e):
            if e["id"] != c["id"]:
                return False
            if c["id"] in FILE_IDS and e["key"] is not None:   # 文件级写了路径：按路径匹配，不看 modules
                return e["key"] == c.get("key")
            return e["modules"] == c["modules"] and (e["key"] is None or e["key"] == c.get("key", ""))
        return any(hit(e) for e in valid_ignore)

    all_c = cands.values()
    kept = [c for c in all_c if not ignored(c)]
    skipped = [c for c in all_c if ignored(c)]
    ignored_files = {(c["id"], c.get("key")) for c in skipped if c["id"] in FILE_IDS}   # 被忽略的不再进热点文件
    for h in hot_files:
        h["long"] = h["long"] and ("M18", h["path"]) not in ignored_files
        h["fix_hot"] = h["fix_hot"] and ("M19", h["path"]) not in ignored_files
    hot_files = sorted((h for h in hot_files if h["long"] or h["fix_hot"]),
                       key=lambda h: (0 if h["long"] and h["fix_hot"] else 1, -h["fixes"], -h["nloc"], h["path"]))
    comparison = None
    if previous is not None:
        prev = {mm.rename_fp(c["fingerprint"]) for c in previous.get("candidates", [])
                if isinstance(c, dict) and isinstance(c.get("fingerprint"), str)}
        cur = {c["fingerprint"] for c in kept}
        ign = {c["fingerprint"] for c in skipped}
        gone = prev - cur - ign
        comparison = {"new": sorted(cur - prev),
                      "fixed": sorted(fp for fp in gone if fp.split("|")[0] not in unchecked),
                      "still": sorted(cur & prev), "ignored_now": sorted(prev & ign),
                      "unchecked": sorted(fp for fp in gone if fp.split("|")[0] in unchecked)}
        pth = previous.get("thresholds")
        if isinstance(pth, dict) and pth != th:
            diff = sorted(k for k in set(pth) | set(th) if pth.get(k) != th.get(k))
            notes.append(f"阈值与上期不同（{', '.join(diff)}），新增 / 已修的对比需谨慎解读")

    modules_out = [
        {"name": n, "role": mm.role(n), "layer": mm.modules[n].get("layer"), "nloc": nloc[n], "lines": lines[n],
         "share": round(nloc[n] / total, 3), "ca": ca[n], "ce": ce[n], "instability": round(inst[n], 2),
         "churn": churn[n], "fixes": fixes[n], "dup_lines": dup.get(n, 0)}
        for n in names
    ]
    summary = {
        "modules": len(names),
        "module_edges": len(medges),
        "cycles": len(sccs),
        "modules_in_cycles": sum(len(c) for c in sccs),
        "violation_edges": len(violations),
        "change_amplification": round(sum(feat_spread) / len(feat_spread), 2) if feat_spread else None,
        "commits_used": used,
        "repo_fix_ratio": round(repo_fix, 3),
        "total_nloc": sum(nloc.values()),
        "duplication_checked": raw.get("clones") is not None,
        "candidates_by_id": dict(sorted(Counter(c["id"] for c in kept).items())),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": raw["repo"],
        "head": raw.get("head"),
        "since": raw.get("since"),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "notes": notes,
        "unchecked_ids": sorted(unchecked),
        "map_warnings": mm.warnings,
        "renames": mm.renames,
        "thresholds": th,
        "summary": summary,
        "modules": modules_out,
        "module_edges": [
            {"src": s, "dst": d, "count": len(el), "evidence": ev(el)} for (s, d), el in sorted(medges.items())
        ],
        "candidates": kept,
        "ignored": skipped,
        "invalid_ignores": invalid_ignore,
        "comparison": comparison,
        "unmapped_files": sorted(unmapped)[:100],
        "unmapped_count": len(unmapped),
        "hot_files": hot_files[:100],
        "hot_files_count": len(hot_files),
    }


# ---------------------------------------------------------------- compare（AI 复核结果的两期对比）

def compare_reviewed(prev: dict, cur: dict, renames: dict | None = None, ignored: set | None = None,
                     candidates: set | None = None, unchecked_ids: set | None = None) -> dict:
    """reviewed.json：{"items": [{"title", "fingerprints": [...], "status": "confirmed" | "rejected"}]}。

    一条发现的任一成员指纹在两期出现即视为同一条。上期确认的发现，只有在本期的复核结果、
    脚本候选（findings.json 的 candidates）和忽略名单里都不再出现，才算「已修」；
    仍在其中任一处但本期没确认的，记为「改判」。
    """
    renames = renames or {}

    def fps(item, translate=False):
        out = set()
        for fp in item.get("fingerprints") or []:
            if not isinstance(fp, str):
                continue
            if translate and renames:
                parts = fp.split("|")
                if len(parts) >= 2:
                    parts[1] = "+".join(sorted(renames.get(x, x) for x in parts[1].split("+") if x))
                fp = "|".join(parts)
            out.add(fp)
        return out

    def title(item):
        return item.get("title") or ",".join(sorted(fps(item)))

    prev_items = [i for i in prev.get("items") or [] if isinstance(i, dict)]
    cur_items = [i for i in cur.get("items") or [] if isinstance(i, dict)]
    prev_conf = [(i, fps(i, True)) for i in prev_items if i.get("status") == "confirmed"]
    cur_conf = [(i, fps(i)) for i in cur_items if i.get("status") == "confirmed"]
    cur_any = set(ignored or set()) | set(candidates or set())
    for i in cur_items:
        cur_any |= fps(i)
    unchecked_ids = set(unchecked_ids or set())

    def all_unchecked(fs):   # 成员指纹的编号本期全都没查：不能判「已修」
        return bool(fs) and all(f.split("|")[0] in unchecked_ids for f in fs)

    gone = [(p, pf) for p, pf in prev_conf if not (pf & cur_any)]
    return {
        "new": [title(i) for i, f in cur_conf if not any(f & pf for _, pf in prev_conf)],
        "still": [title(i) for i, f in cur_conf if any(f & pf for _, pf in prev_conf)],
        "fixed": [title(p) for p, pf in gone if not all_unchecked(pf)],
        "reclassified": [title(p) for p, pf in prev_conf if (pf & cur_any) and not any(pf & f for _, f in cur_conf)],
        "unchecked": [title(p) for p, pf in gone if all_unchecked(pf)],
    }


# ---------------------------------------------------------------- CLI

def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump(obj, path):
    """原子写入：先写临时文件再改名；无法编码的字符（如非 UTF-8 文件名）转义保存。"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="架构坏味道体检：采集证据 / 分析候选 / 对比两期结果")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="抽取依赖、代码量、git 历史、重复片段")
    c.add_argument("repo")
    c.add_argument("--out", required=True)
    c.add_argument("--since", default="6 months ago", help='git 统计窗口，默认 "6 months ago"；传空串表示全部历史')
    c.add_argument("--no-git", action="store_true", help="不读 git（没有演化信号）")
    c.add_argument("--no-jscpd", action="store_true", help="不跑重复代码检测（M17 本期未查）")
    c.add_argument("--jscpd-timeout", type=int, default=600, help="jscpd 超时秒数，超时整组进程杀掉")
    a = sub.add_parser("analyze", help="按模块地图聚合并产出候选发现")
    a.add_argument("raw")
    a.add_argument("--modules", help="AI 确认的 module-map.json；省略则用 raw.json 里的草稿")
    a.add_argument("--ignore", help=".arch-ignore.yaml")
    a.add_argument("--previous", help="上期 findings.json")
    a.add_argument("--threshold", action="append", default=[], metavar="KEY=VALUE", help="临时覆盖阈值")
    a.add_argument("--out", required=True)
    p = sub.add_parser("compare", help="对比两期 AI 复核后的 reviewed.json")
    p.add_argument("--previous", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--findings", required=True, help="本期 findings.json（提供候选、被忽略的指纹与改名映射）")
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "collect":
        try:
            raw = collect(args.repo, since=args.since or None, use_git=not args.no_git,
                          use_jscpd=not args.no_jscpd, jscpd_timeout=args.jscpd_timeout)
        except FileNotFoundError as exc:
            ap.error(str(exc))
        _dump(raw, args.out)
        print(f"files={len(raw['files'])} edges={len(raw['edges'])} commits={len(raw['commits'])} "
              f"clones={'-' if raw['clones'] is None else len(raw['clones'])} draft_modules={len(raw['draft_modules'])}")
        for note in raw["notes"]:
            say(f"note: {note}")
        return 0

    if args.cmd == "compare":
        try:
            prev, cur = _load_json(args.previous), _load_json(args.current)
            findings = _load_json(args.findings)
        except (OSError, ValueError) as exc:
            ap.error(f"读取 reviewed.json / findings.json 失败：{exc}")

        def fps(key):
            return {c["fingerprint"] for c in findings.get(key, []) if isinstance(c, dict) and "fingerprint" in c}

        result = compare_reviewed(prev, cur, renames=findings.get("renames") or {}, ignored=fps("ignored"),
                                  candidates=fps("candidates"), unchecked_ids=set(findings.get("unchecked_ids") or []))
        _dump(result, args.out)
        print(" ".join(f"{k}={len(v)}" for k, v in result.items()))
        return 0

    thresholds = {}
    for item in args.threshold:
        k, sep, v = item.partition("=")
        if not sep or k not in DEFAULT_THRESHOLDS:
            ap.error(f"unknown threshold {item!r}; known: {', '.join(DEFAULT_THRESHOLDS)}")
        try:
            thresholds[k] = type(DEFAULT_THRESHOLDS[k])(v)
        except ValueError:
            ap.error(f"threshold {k} 需要 {type(DEFAULT_THRESHOLDS[k]).__name__}，收到 {v!r}")
    if args.previous and not os.path.exists(args.previous):
        ap.error(f"--previous 文件不存在：{args.previous}（首次体检请省略该参数）")
    try:
        raw = _load_json(args.raw)
        spec = _load_json(args.modules) if args.modules else None
        previous = _load_json(args.previous) if args.previous else None
    except (OSError, ValueError) as exc:
        ap.error(f"读取输入失败：{exc}")
    try:
        ignore = load_ignore(args.ignore)
    except Exception as exc:  # noqa: BLE001 — YAML 语法错误等一律按参数错误报告
        ap.error(f"忽略名单无法读取：{type(exc).__name__}: {exc}")
    try:
        result = analyze(raw, map_spec=spec, ignore=ignore, previous=previous, thresholds=thresholds)
    except ValueError as exc:
        ap.error(str(exc))
    _dump(result, args.out)
    s = result["summary"]
    print(f"modules={s['modules']} edges={s['module_edges']} cycles={s['cycles']} "
          f"violation_edges={s['violation_edges']} candidates={s['candidates_by_id']}")
    for w in result["map_warnings"]:
        say(f"warning: module-map: {w}")
    if result["invalid_ignores"]:
        say(f"warning: {len(result['invalid_ignores'])} ignore entries without id/reason/modules were not applied")
    if result["unmapped_count"]:
        say(f"warning: {result['unmapped_count']} code files not in any module")
    for note in result["notes"]:
        say(f"note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
