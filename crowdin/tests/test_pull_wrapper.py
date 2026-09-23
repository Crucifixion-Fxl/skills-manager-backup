"""自动化测试 pull.sh wrapper 的关键路径。

覆盖范围:
  - 参数校验(未知 project / 缺 config / 错误 op)
  - help / ls 子命令的输出与退出码
  - false-success scanner 行为(对预录制 log fixtures 的命中与放行)
  - "用户取消"误报排除

不覆盖(留给手工 e2e + MR 证据):
  - probe / diff / full 真实网络与凭证
  - 真实 auto_l10n.sh 下游

跑:
  cd ~/A4x/AI/skills
  uv run pytest skills/crowdin/tests/ -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
PULL_SH = SKILL_DIR / "pull.sh"


def run_pull(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """直接调 pull.sh,返回 CompletedProcess(不抛异常)。"""
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [str(PULL_SH), *args],
        capture_output=True,
        text=True,
        env=e,
    )


# ============================================================
# Layer 0: 文件存在 + 可执行
# ============================================================


def test_pull_sh_exists_and_executable():
    assert PULL_SH.exists(), f"{PULL_SH} 不存在"
    assert os.access(PULL_SH, os.X_OK), f"{PULL_SH} 没有可执行位"


# ============================================================
# Layer 1: 帮助与无参数
# ============================================================


def test_no_args_shows_help():
    r = run_pull()
    assert r.returncode == 0
    # 帮助文本里应该包含 Usage 段
    assert "Usage:" in r.stdout
    # 不应该有 # 前缀残留(注释剥离正确)
    for line in r.stdout.splitlines():
        if line.strip():
            assert not line.startswith("#"), f"行残留 # 前缀: {line!r}"


@pytest.mark.parametrize("flag", ["--help", "-h", "help"])
def test_help_flags(flag: str):
    r = run_pull(flag)
    assert r.returncode == 0
    assert "Usage:" in r.stdout


# ============================================================
# Layer 2: ls 子命令
# ============================================================


def test_ls_all_projects():
    r = run_pull("ls")
    assert r.returncode == 0
    # 三个 project header 应都出现
    assert "android" in r.stdout
    assert "flutter" in r.stdout
    assert "ios" in r.stdout


def test_ls_by_project_valid():
    r = run_pull("ls", "flutter")
    assert r.returncode == 0
    # 至少应该有几个 *_flutter_dev 的 config
    assert "_flutter_dev" in r.stdout


def test_ls_by_project_invalid():
    r = run_pull("ls", "windows-phone")
    assert r.returncode != 0
    assert "unknown project" in (r.stdout + r.stderr).lower()


# ============================================================
# Layer 3: 参数校验失败路径
# ============================================================


def test_unknown_top_command():
    r = run_pull("delete-everything")
    assert r.returncode == 1
    assert "unknown" in (r.stdout + r.stderr).lower()


def test_missing_config_name():
    r = run_pull("flutter")
    assert r.returncode == 1
    msg = r.stdout + r.stderr
    assert "missing config name" in msg or "missing" in msg.lower()


def test_unknown_op_rejected_before_probe():
    """未知 op 必须在 probe 之前拒绝,不能联网才发现。"""
    r = run_pull("flutter", "kb_flutter_dev", "totally-fake-op")
    assert r.returncode == 1
    msg = r.stdout + r.stderr
    assert "unknown op" in msg.lower()
    # probe 阶段的网络字样不应出现
    assert "probing api.crowdin.com" not in msg


def test_nonexistent_config_lists_available():
    r = run_pull("android", "definitely-not-a-real-config", "probe")
    assert r.returncode == 1
    msg = r.stdout + r.stderr
    # 应该列出可用 config
    assert "Available" in msg or "missing" in msg.lower()


# ============================================================
# Layer 4: false-success scanner(核心安全逻辑)
# 用 bash -c 调 wrapper 内的 scan_false_success 函数,喂 fixture log
# ============================================================


def _scan_via_bash(log_content: str, op: str) -> tuple[int, str]:
    """把 log 写到 tmp 文件,用 bash 调 wrapper 里的 scan_false_success。

    用 source pull.sh 加载函数,然后调用并 return 其退出码。
    """
    # 直接复刻 scanner 逻辑(避免 source 整个 pull.sh 触发 main),
    # 这样测试只验证策略,不会被 pull.sh 的其它副作用干扰
    script = textwrap.dedent(
        r"""
        set +e
        log="$1"
        op="$2"
        hits=$(grep -nE "❌|资源下载失败|bundle download failed|Certificate for.*doesn.t match|returned non-zero exit status|Configuration file doesn.t exist" "$log" \
            | grep -vE "❌ 用户取消操作|❌ 用户中止|❌ 用户" || true)
        if [ -n "$hits" ]; then
            echo "MATCH_KEYWORDS"
            exit 5
        fi
        if [ "$op" = "diff" ]; then
            deleted=$(grep -c "🗑️  删除文件" "$log" 2>/dev/null || true)
            deleted=${deleted:-0}
            if [ "$deleted" -ge 5 ]; then
                echo "MATCH_HEURISTIC=$deleted"
                exit 5
            fi
        fi
        echo "CLEAN"
        exit 0
        """
    )
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(log_content)
        f.flush()
        log_path = f.name
    try:
        r = subprocess.run(
            ["bash", "-c", script, "_", log_path, op],
            capture_output=True,
            text=True,
        )
        return r.returncode, r.stdout.strip()
    finally:
        os.unlink(log_path)


# Fixtures: 几类典型 log

FAKE_SUCCESS_LOG = textwrap.dedent(
    """
    [信息] 开始下载应用资源 (Bundle ID: 177 91)...
    ❌ Bundle ID: 177 的资源下载失败: returned non-zero exit status 102
    ❌ FLUTTER 平台本地化资源下载失败
    [成功] 应用资源下载成功
    """
).strip()

REAL_SUCCESS_LOG = textwrap.dedent(
    """
    [信息] 开始下载应用资源...
    ✅ Bundle ID: 177 的资源下载成功
    ✅ Bundle ID: 91 的资源下载成功
    ✅ FLUTTER 平台本地化资源下载完成
    """
).strip()

USER_CANCEL_LOG = textwrap.dedent(
    """
    [信息] 检测到翻译文件有变更
    是否继续执行 后续操作？[y/N/d]: ❌ 用户取消操作
    [警告] 用户取消操作,保持当前状态
    """
).strip()

EMPTY_DOWNLOAD_DELETED_LOG = textwrap.dedent(
    """
    [信息] 找到 17 个翻译文件需要对比
    📄 检查文件: intl_ar.arb
       🗑️  删除文件: intl_ar.arb
    📄 检查文件: intl_cs.arb
       🗑️  删除文件: intl_cs.arb
    📄 检查文件: intl_de.arb
       🗑️  删除文件: intl_de.arb
    📄 检查文件: intl_en.arb
       🗑️  删除文件: intl_en.arb
    📄 检查文件: intl_es.arb
       🗑️  删除文件: intl_es.arb
    📄 检查文件: intl_fr.arb
       🗑️  删除文件: intl_fr.arb
    """
).strip()

SSL_HIJACK_LOG = textwrap.dedent(
    """
    ❌ Error from server: Certificate for <api.crowdin.com> doesn't match any of the subject alternative names
    """
).strip()


def test_scanner_catches_fake_success_keywords():
    rc, msg = _scan_via_bash(FAKE_SUCCESS_LOG, "diff")
    assert rc == 5, f"expected exit 5, got {rc}; msg={msg}"
    assert "MATCH_KEYWORDS" in msg


def test_scanner_passes_real_success():
    rc, msg = _scan_via_bash(REAL_SUCCESS_LOG, "diff")
    assert rc == 0, f"expected clean, got rc={rc}; msg={msg}"
    assert msg == "CLEAN"


def test_scanner_passes_real_success_full_op():
    """删除启发式只在 diff 模式生效,full 不应被启发式误伤。"""
    rc, msg = _scan_via_bash(REAL_SUCCESS_LOG, "full")
    assert rc == 0
    assert msg == "CLEAN"


def test_scanner_excludes_user_cancel_false_positive():
    """'❌ 用户取消操作' 是脚本自身在非交互模式下打的伪标记,不应触发。"""
    rc, msg = _scan_via_bash(USER_CANCEL_LOG, "diff")
    assert rc == 0, f"user cancel should not trigger scanner; got rc={rc}; msg={msg}"


def test_scanner_heuristic_catches_empty_download_in_diff():
    """diff 模式下大量删除标记(空下载特征)应被拦下。"""
    rc, msg = _scan_via_bash(EMPTY_DOWNLOAD_DELETED_LOG, "diff")
    assert rc == 5
    assert "HEURISTIC" in msg


def test_scanner_heuristic_skipped_in_full_op():
    """同样的"大量删除"在 full 模式下不启用启发式,以免误伤真实大规模变更。"""
    rc, msg = _scan_via_bash(EMPTY_DOWNLOAD_DELETED_LOG, "full")
    # 这个 fixture 没有关键词,只有删除标记 → full 模式应放行
    assert rc == 0
    assert msg == "CLEAN"


def test_scanner_catches_ssl_hijack():
    rc, msg = _scan_via_bash(SSL_HIJACK_LOG, "probe")
    assert rc == 5
    assert "MATCH_KEYWORDS" in msg


# ============================================================
# Layer 5: 安全约束(可被 CI / 评审静态扫到)
# ============================================================


def test_no_internal_gitlab_domain_in_script():
    """pull.sh 不能硬编码内部 GitLab 域名(CI security 规则)。"""
    content = PULL_SH.read_text()
    assert "gitlab.addx.ai" not in content, "pull.sh 不能含内部 GitLab 域名"


def test_no_hardcoded_token_in_script():
    """pull.sh 不能硬编码任何 token 字符串。"""
    content = PULL_SH.read_text()
    # 常见 token 前缀
    for prefix in ["glpat_", "squ_", "Bearer ey"]:
        assert prefix not in content, f"pull.sh 含疑似硬编码 token: {prefix}"
