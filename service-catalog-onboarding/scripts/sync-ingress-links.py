#!/usr/bin/env python3
"""sync-ingress-links —— 从 k8s ingress.yaml 派生 Component links 的建议输出。

用法：
  python3 sync-ingress-links.py <repo-root>

行为（起步版）：
  扫 <repo-root> 下所有 k8s/**/ingress*.yaml（跳 .claude/ / node_modules/ / .git/）；
  从每个 Ingress 的 spec.rules[].host 提取 host；
  反查仓根 catalog-info.yaml 找出和 host 相关的 Component（按 ingress metadata.name 或目录名匹配）；
  **只对 spec.type == "website" 的 Component 输出"建议加 link"**——backend 微服务（type: service）的
  ingress host 是内部服务间路由 / API Gateway 入口，不是浏览器 URL，不打 🌐 web link
  （API 入口走 kind: API 实体的 definition.$text）。
  根据路径中 overlay 名（prod-* / staging-* / dev-*）推断 environment 标签。

目前只输出建议到 stdout，不直接改 catalog-info.yaml ——
yq 类工具的 comment-preserving + 多 doc YAML 写回坑较多，
让人/PR review 决定要不要写。后续 CI gate 化时再实现 in-place 写回。

退出码：0 = 正常（无论有没有建议）；2 = repo-root 不存在 / 无 catalog-info.yaml。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import yaml  # PyYAML; 仓内 venv / 系统 python3 一般都有
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)


SKIP_DIRS = {".claude", "node_modules", ".git", "__pycache__", "vendor"}
INGRESS_FILE_RE = re.compile(r"^ingress(-.*)?\.ya?ml$")
# overlay 路径里 prod / staging / dev 标识
ENV_RE = re.compile(r"/overlays/(prod|staging|dev|preview)[-/]")


def is_ingress(yaml_doc: dict) -> bool:
    return (
        isinstance(yaml_doc, dict)
        and yaml_doc.get("kind") == "Ingress"
        and isinstance(yaml_doc.get("spec"), dict)
    )


def extract_hosts(ingress: dict) -> list[str]:
    rules = ingress.get("spec", {}).get("rules") or []
    hosts = []
    for r in rules:
        h = r.get("host") if isinstance(r, dict) else None
        if h:
            hosts.append(h)
    return hosts


def env_from_path(path: Path) -> str:
    m = ENV_RE.search(str(path))
    return m.group(1) if m else "base"


def find_ingress_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        # in-place prune skipped dirs
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "/k8s/" not in dirpath + "/" and not dirpath.endswith("/k8s"):
            continue
        for fn in filenames:
            if INGRESS_FILE_RE.match(fn):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def load_catalog(root: Path) -> list[dict]:
    f = root / "catalog-info.yaml"
    if not f.exists():
        print(f"ERROR: {f} not found", file=sys.stderr)
        sys.exit(2)
    with f.open() as fh:
        docs = list(yaml.safe_load_all(fh))
    return [d for d in docs if isinstance(d, dict)]


def website_components(docs: list[dict]) -> dict[str, dict]:
    """返回 {component-name: doc} —— 只含 spec.type == 'website' 的 Component。"""
    out = {}
    for d in docs:
        if d.get("kind") != "Component":
            continue
        if (d.get("spec") or {}).get("type") != "website":
            continue
        name = (d.get("metadata") or {}).get("name")
        if name:
            out[name] = d
    return out


def match_component(ingress_path: Path, ingress_name: str | None,
                    websites: dict[str, dict]) -> str | None:
    """保守匹配 ingress 到 website Component（避免假阳性 → 把 backend ingress 误认成 website）：
    1) ingress.metadata.name 直接命中 Component name —— 这是首选信号（最强）；
    2) 路径里某个目录段是某 website Component name 的"末段 token"（不接受任意子串模糊匹配）。
       例：k8s/admin/... → token 'admin' 命中 'engagement-admin'；k8s/backend/... → 不命中（'backend' 不是哪个 website 的末段）。
    匹配不到 → 返回 None（caller 输出 [skip]，不打 web link）。
    """
    if ingress_name and ingress_name in websites:
        return ingress_name
    parts = ingress_path.parts
    skip_segments = {"k8s", "overlays", "runtime", "base", "infra", "manifests", "deploy"}
    for p in reversed(parts):
        if p in skip_segments or ENV_RE.search(f"/{p}/"):
            continue
        if p in websites:
            return p
        # 末段 token 匹配（如 'admin' 匹配 'engagement-admin'，但 'backend' / 'engagement' 不匹配 —— 避免误中）
        for name in websites:
            tokens = name.split("-")
            if len(tokens) >= 2 and p == tokens[-1]:
                return name
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: sync-ingress-links.py <repo-root>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 2

    docs = load_catalog(root)
    websites = website_components(docs)
    if not websites:
        print(f"# {root}：catalog 里没有 spec.type: website 的 Component —— 跳过")
        return 0

    ingresses = find_ingress_files(root)
    if not ingresses:
        print(f"# {root}：未发现 k8s/**/ingress*.yaml")
        return 0

    print(f"# sync-ingress-links 建议（root={root}）")
    print(f"# 候选 website Component: {sorted(websites.keys())}")
    print()
    suggestions = 0
    for path in ingresses:
        try:
            with path.open() as fh:
                yaml_docs = list(yaml.safe_load_all(fh))
        except Exception as e:
            print(f"# WARN 解析失败 {path}: {e}")
            continue
        for d in yaml_docs:
            if not is_ingress(d):
                continue
            iname = (d.get("metadata") or {}).get("name")
            hosts = extract_hosts(d)
            if not hosts:
                continue
            comp = match_component(path, iname, websites)
            env = env_from_path(path)
            rel = path.relative_to(root)
            if not comp:
                # 对应不到 website Component —— 大概率是 backend 微服务的 ingress，按规则不打 web link
                kind_hint = "service?" if iname else "?"
                print(f"# [skip] {rel}  host={hosts}  ingress.name={iname}  "
                      f"匹配到的 Component 不是 website（推测 type={kind_hint}）—— 不输出 🌐 link")
                continue
            for host in hosts:
                title = "🌐 Production" if env == "prod" else (
                    "🌐 Staging" if env == "staging" else
                    "🌐 Dev" if env == "dev" else
                    "🌐 Preview" if env == "preview" else
                    f"🌐 {env}"
                )
                print(f"# [suggest] Component: {comp}   (env={env}, ingress={rel})")
                print(f"#   - title: \"{title}\"")
                print(f"#     url: \"https://{host}\"")
                print(f"#     icon: web")
                suggestions += 1

    print()
    print(f"# Done. {suggestions} link suggestion(s) for {len(websites)} website Component(s).")
    print("# NOTE: 不直接改 catalog-info.yaml；根据建议人工或 PR review 决定。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
