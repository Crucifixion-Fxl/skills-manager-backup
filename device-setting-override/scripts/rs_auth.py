#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rs_auth.py — device-setting-override skill 的 revenue-sharing LDAP 鉴权共享模块。

Shared LDAP auth module for the device-setting-override skill scripts.

职责 / Responsibilities:
  1. 路由一致性: --env / --rs-env / --node-name 三者环境段必须一致; 缺省自动推导,
     显式冲突直接拒绝 (2026-10-08 事故: staging RS + node-name=prod-* → 400/102
     accessKey无效, 因为 RS 用自己环境的 PaasConfig key 签名, 而 accessKey 注册表
     按集群隔离)。
  2. token 解析优先级: 显式环境变量(默认 SKILL_LDAP_TOKEN) > 本地缓存 > 自动登录。
  3. token 缓存: ~/.cache/device-setting-override/token-<rs_env>.json, 0600 权限,
     按 login 返回的 expiresInSeconds 续期 (提前 5 分钟视为过期)。
  4. 自动登录: 环境变量 RS_LDAP_CN + RS_LDAP_PASSWORD 存在时静默调
     POST <rs_host>/skill-api/login 换 token 并写缓存。
  5. 降级: 无密码环境变量且无有效缓存时, 打印手动登录指引后以 exit code 3 退出
     (skills validator 禁交互式 stdin, 用"打印指引"替代 prompt)。

secret 规则 / Secret policy (用户 2026-10-08 批准修订):
  - 密码 (RS_LDAP_PASSWORD) 绝不落盘、绝不打印、绝不进日志。
  - token 是短期凭据 (TTL 以登录响应 expiresInSeconds 为准, 缺省 3 天),
    允许 0600 本地缓存 (与 ~/.kube/config 同级做法)。
  - 显式 SKILL_LDAP_TOKEN 环境变量仍然最高优先, 行为与旧版完全一致。
"""

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# 常量 / Constants
# ---------------------------------------------------------------------------

# 自动登录凭据的环境变量名 (CN 非机密可放 shell profile; 密码仅当前会话 export, 绝不落盘)。
CN_ENV = "RS_LDAP_CN"
PASSWORD_ENV = "RS_LDAP_PASSWORD"

# login 响应的默认 TTL (秒); 实际以响应里的 expiresInSeconds 为准。
TOKEN_TTL_DEFAULT = 259200  # 3 天

# 缓存 token 提前这么多样视为过期, 避免"刚取出来就失效"的边界。
RENEW_MARGIN_SECONDS = 300

# 降级退出码 (2 已被 argparse 的参数错误占用; 3 = 需要人工登录)。
EXIT_MANUAL_LOGIN = 3

# node-name 合法环境段 (与 RS 部署环境一一对应)。
RS_ENV_PREFIXES = ("staging", "prod")

# 已声明的合法 node-name 全集 (与 SKILL.md rs-env 表及各脚本 ENV_HOSTS 键一致)。
# resolve_ldap_routing 据此拒绝未声明节点 (如 prod-unknown) —— 前缀对但节点名
# 未登记的值会被服务端拒绝, 本地提前拦下并给出合法词表。 (来源:CI code-review r2 P2)
NODE_NAMES = ("staging-us", "staging-eu", "staging-cn",
              "prod-us", "prod-eu", "prod-cn")

# revenue-sharing EResponse.UN_LOGIN 的业务码。
# 实测 (2026-10-08): token 失效时部署版 RS 返回 HTTP 200 + {"code":101,"msg":"未登录"},
# 并非 GlobalExceptionHandler 注释里的 401 —— 两种形态都要认。
UN_LOGIN_CODE = 101


def looks_unauthenticated(status_code: int, body: Optional[dict]) -> bool:
    """判断一次 /skill-api/* 响应是否为 token 失效 (决定是否自动重登重试)。

    认两种形态: HTTP 401, 或 (任意 HTTP 码 + 信封 code/result == 101)。
    body 传已解析的 dict (解析失败传 None)。
    """
    if status_code == 401:
        return True
    if isinstance(body, dict):
        return body.get("code") == UN_LOGIN_CODE or body.get("result") == UN_LOGIN_CODE
    return False


class ManualLoginRequired(Exception):
    """无可用 token 且无法自动登录; str(e) 是给用户看的手动登录指引。"""


class RouteMismatch(Exception):
    """--env / --rs-env / --node-name 环境段不一致; str(e) 是人话解释。"""


# ---------------------------------------------------------------------------
# 路由一致性 / Routing consistency
# ---------------------------------------------------------------------------

def resolve_ldap_routing(env: Optional[str], rs_env: Optional[str],
                         node_name: Optional[str]) -> Tuple[str, str, str]:
    """校验并补全 ldap 模式的三个路由参数, 返回 (env, rs_env, node_name)。

    规则:
      - node_name 缺省时取 env (两者词表相同: staging-us/eu/cn, prod-us/eu/cn)。
      - rs_env 缺省时从 node_name 的环境段推导 (prod-us → prod)。
      - 任何显式给出的值与推导结果冲突 → RouteMismatch (绝不静默按错误组合发请求)。
    """
    if not node_name and not env:
        raise RouteMismatch(
            "ldap 模式至少给出 --env 或 --node-name 之一 / "
            "at least one of --env / --node-name is required")
    if not node_name:
        node_name = env
    if not env:
        env = node_name
    if env != node_name:
        raise RouteMismatch(
            f"--env={env} 与 --node-name={node_name} 不一致: ldap 模式下真实路由由 "
            f"--node-name 决定, --env 仅是日志标签, 两者必须相同以免误导排查。"
            f"/ log-label env must equal routing node-name")
    if node_name not in NODE_NAMES:
        raise RouteMismatch(
            f"--node-name={node_name} 不是已声明的节点 / unknown node-name "
            f"(合法值: {'/'.join(NODE_NAMES)}); 拼错的节点名只会被服务端拒绝, "
            f"这里提前拦下。/ node-name must be one of the declared names")
    prefix = node_name.split("-", 1)[0] if "-" in node_name else ""
    if prefix not in RS_ENV_PREFIXES:
        raise RouteMismatch(
            f"无法从 --node-name={node_name} 推导 rs-env (合法前缀: "
            f"{'/'.join(RS_ENV_PREFIXES)}); 请检查拼写 / cannot derive rs-env prefix")
    if rs_env is None:
        rs_env = prefix
    elif rs_env != prefix:
        raise RouteMismatch(
            f"--rs-env={rs_env} 与 --node-name={node_name} 环境段冲突: revenue-sharing "
            f"用『自己部署环境』的 accessKey 签名, 而 accessKey 注册表按集群隔离 —— "
            f"{rs_env} 的凭据只在 {rs_env} 集群注册, 打 {node_name} 必然返回 "
            f"HTTP 400 code=102 accessKey无效 (2026-10-08 实证)。"
            f"请改用 --rs-env={prefix} 或省略 --rs-env 自动推导。"
            f"/ rs-env must match node-name cluster tier")
    return env, rs_env, node_name


# ---------------------------------------------------------------------------
# token 缓存 / Token cache
# ---------------------------------------------------------------------------

def _cache_dir() -> Path:
    """缓存目录; 测试可用 RS_AUTH_CACHE_DIR 重定向。"""
    override = os.environ.get("RS_AUTH_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "device-setting-override"


def _cache_path(rs_env: str) -> Path:
    return _cache_dir() / f"token-{rs_env}.json"


def load_cached_token(rs_env: str, now: Optional[float] = None,
                      expect_cn: Optional[str] = None) -> Optional[str]:
    """读缓存; 不存在 / 损坏 / 已到期(含提前量) / 权限不安全 / 主体不符 一律返回 None。

    fail-closed 安全检查 (来源:CI code-review r2 P2) —— 写入侧虽创建 0600, 但若缓存
    文件事后被放宽权限 / 换 owner / 换成符号链接, 仍拒绝使用 (视为未命中, 下次登录
    写入时会被 0600 原子替换):
      - 缓存目录 lstat 非常规目录 (含目录级符号链接) / group/other 位有权限 → None;
      - 文件 lstat 非常规文件 (含符号链接) → None;
      - group/other 位有任何权限 (mode & 0o077) → None;
      - owner != 当前 euid (平台有 geteuid 时) → None。

    主体绑定 (来源:CI code-review r6 P1) —— 缓存按 rs-env 命名但不绑定 LDAP 主体时,
    同机切换 RS_LDAP_CN 会复用旧账号 token (审计/授权主体错位)。写入侧把登录响应
    确认的 ldapCn 存进缓存; expect_cn 给出时 (自动登录场景 = 环境变量 CN), 缓存
    ldapCn 与之不符 → 视为未命中 (重新登录换取新主体 token)。旧格式无 ldapCn 字段
    且 expect_cn 给出 → fail-closed 同样视为未命中。
    """
    path = _cache_path(rs_env)
    cache_dir = path.parent
    try:
        dst = os.lstat(cache_dir)
        if not stat.S_ISDIR(dst.st_mode) or (dst.st_mode & 0o077):
            return None
        if hasattr(os, "geteuid") and dst.st_uid != os.geteuid():
            return None
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if st.st_mode & 0o077:
        return None
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        token = raw["token"]
        expires_at = float(raw["expiresAt"])
        cached_cn = raw.get("ldapCn")
    except Exception:
        return None
    # token 必须是非空 str (来源:CI code-review r3 建议 4 —— dict/int 等形状拒绝)。
    if not isinstance(token, str) or not token or not isinstance(expires_at, (int, float)):
        return None
    # 主体绑定: 期望主体已知时, 缓存主体必须精确一致 (含旧格式无 ldapCn 的情况)。
    if expect_cn is not None and cached_cn != expect_cn:
        return None
    now = time.time() if now is None else now
    if now >= expires_at - RENEW_MARGIN_SECONDS:
        return None
    return token


def invalidate_cached_token(rs_env: str) -> None:
    """删除缓存 (401 重试前调用)。幂等; OSError 包成 RuntimeError 不裸穿。"""
    try:
        _cache_path(rs_env).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        raise RuntimeError(f"token 缓存删除失败 / cache unlink failed: {e}")


def _verified_cache_dir() -> Path:
    """返回 lstat 校验通过的缓存目录; 不存在则以 0700 创建。

    写入侧 fail-closed (来源:CI code-review r7 P1) —— 与读取侧同标准:
    目录是符号链接 / group/other 位有权限 / owner 非当前用户 → RuntimeError,
    防止缓存目录被换成指向攻击者可读目录的符号链接后, 自动登录的新 token
    被写进攻击者可读位置 (读取侧拒绝形同虚设)。
    """
    d = _cache_dir()
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        d.mkdir(parents=True, mode=0o700)
        os.chmod(d, 0o700)
        st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode):   # lstat: symlink 不是目录 → 拒绝
        raise RuntimeError(
            f"缓存目录不是真实目录(拒绝符号链接) / cache dir is symlink: {d}")
    if st.st_mode & 0o077:
        raise RuntimeError(
            f"缓存目录权限过宽 / cache dir too permissive: "
            f"{oct(stat.S_IMODE(st.st_mode))}")
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise RuntimeError(
            f"缓存目录 owner 非当前用户 / cache dir foreign owner: {d}")
    return d


def _write_cache_file_atomic(d: Path, rs_env: str, payload: dict) -> None:
    """把 payload 原子写入 <d>/token-<rs_env>.json。

    平台支持 dir_fd 时 (macOS/Linux): 打开目录描述符 (O_NOFOLLOW 再拒一次
    符号链接) 后所有路径操作相对该 fd —— 目录在校验后被调包 (TOCTOU) 也
    无法把文件导向别处。不支持的平台回落 mkstemp + replace。
    """
    final_name = f"token-{rs_env}.json"
    _dirfd_ok = (os.open in os.supports_dir_fd
                 and os.replace in os.supports_dir_fd)
    if _dirfd_ok:
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) \
            | getattr(os, "O_NOFOLLOW", 0)
        dfd = os.open(str(d), dir_flags)
        try:
            fd = os.open(f".token-{rs_env}.{os.getpid()}.tmp",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dfd)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False))
                os.replace(f".token-{rs_env}.{os.getpid()}.tmp", final_name,
                           src_dir_fd=dfd, dst_dir_fd=dfd)
            finally:
                try:
                    os.unlink(f".token-{rs_env}.{os.getpid()}.tmp", dir_fd=dfd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(dfd)
        return
    # 回退路径 (无 dir_fd 支持): mkstemp 0600 + 原子 replace。
    import tempfile
    fd, tmp_name = tempfile.mkstemp(prefix=f".token-{rs_env}.",
                                    suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp_name, str(d / final_name))
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _save_cached_token(rs_env: str, token: str, ttl_seconds: int,
                       now: Optional[float] = None,
                       ldap_cn: Optional[str] = None) -> None:
    """以 0600 权限原子写入缓存 (mkstemp + rename, 避免半写与并发竞态)。

    - mkstemp 本身以 0600 创建 → 无 "先 0644 后 chmod" 的明文窗口;
    - 目录经 _verified_cache_dir 校验 (r7 P1: 写入侧同读取侧标准, 拒绝
      目录符号链接/宽权限/异 owner), 文件写入经 dirfd 相对路径防 TOCTOU 调包;
    - ldap_cn 记录登录响应确认的 LDAP 主体, 读侧据此拒绝跨账号复用 (r6 P1);
    - OSError 统一包成 RuntimeError, 让调用方 (ManualLoginRequired, RuntimeError)
      的既有 catch 能兜住, 不裸穿 traceback。
    """
    now = time.time() if now is None else now
    payload = {
        "rsEnv": rs_env,
        "token": token,
        # 登录响应确认的 LDAP 主体 (无则回落提交 cn); 读侧主体绑定用。
        "ldapCn": ldap_cn,
        # 绝对到期时刻 (epoch 秒) = now + ttl。
        "expiresAt": now + max(1, int(ttl_seconds or TOKEN_TTL_DEFAULT)),
    }
    try:
        d = _verified_cache_dir()
        _write_cache_file_atomic(d, rs_env, payload)
    except RuntimeError:
        raise
    except OSError as e:
        raise RuntimeError(f"token 缓存写入失败 / cache write failed: {e}")


# ---------------------------------------------------------------------------
# 登录 / Login
# ---------------------------------------------------------------------------

def _redact(text: str, secret: str) -> str:
    """把 secret 子串替换为 ***; 防上游错误信息回显请求字段时把密码带进异常文案。"""
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def http_login(rs_env: str, rs_host: str, cn: str, password: str,
               timeout: int = 20) -> dict:
    """调 POST <rs_host>/skill-api/login, 返回 data dict (token/ldapCn/expiresInSeconds)。

    stdlib 实现 (urllib), 不引入 requests —— verify.py 等纯 stdlib 脚本也能用。
    失败抛 RuntimeError; 异常文案里的服务端 msg 会先脱敏 (即使上游回显请求字段
    也不带密码), 顶层/data 非对象时统一转 RuntimeError 不裸穿 AttributeError。
    """
    import urllib.error
    import urllib.request
    body = json.dumps({"ldapCn": cn, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{rs_host}/skill-api/login", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    status = None
    resp_text = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            resp_text = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            resp_text = e.read().decode("utf-8", "replace")
        except Exception:
            resp_text = ""
    except urllib.error.URLError as e:
        raise RuntimeError(f"登录请求失败 / login request failed: {e}")
    try:
        parsed = json.loads(resp_text)
    except ValueError:
        raise RuntimeError(
            f"登录响应不是 JSON / login response not JSON: http={status}")
    body = parsed if isinstance(parsed, dict) else {}
    if status != 200 or body.get("code") != 0:
        # 上游契约未证实的回显风险: msg 先脱敏再限长, 绝不让密码进异常文案。
        # (来源:CI code-review r2 条件性风险, 按防御性直接实装)
        msg = _redact(str(body.get("msg")), password)[:300]
        raise RuntimeError(
            f"登录失败 / login failed: http={status} "
            f"code={body.get('code')} msg={msg}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(
            f"登录响应 data 非对象 / login response data not an object: http={status}")
    token = data.get("token")
    # token 必须是非空 str (来源:CI code-review r8 建议 1 —— 异形成功响应 fail-loud)。
    if not isinstance(token, str) or not token:
        raise RuntimeError(
            "登录成功但响应 token 非法 (须非空字符串) / "
            "login ok but invalid token in response")
    return data


def manual_login_instructions(rs_env: str, rs_host: str) -> str:
    """降级指引: 手动 curl 取 token, 或配置自动登录环境变量。非交互。

    密码绝不进命令行参数 (shell history / ps 进程列表可见): 交互 read -s 后,
    经 printf (shell 内建) 以 NUL 分隔送 stdin, 由 python3 json.dumps 编码
    (printf %s 直拼 JSON 遇引号/反斜杠/换行会产出非法 JSON), curl --data @-。
    (来源:CI code-review r3 P1 红线 + r4 P2 confirmed)
    """
    return (
        f"\n[需要 LDAP 登录 / manual login required] (rs-env={rs_env}, host={rs_host})\n"
        f"方式 A - 手动登录 (token 有效期以响应 expiresInSeconds 为准, 缺省 3 天):\n"
        f"  CN='<你的cn>'; read -rs PW   # 密码交互输入: 不回显、不进 history\n"
        f"  printf '%s\\0%s' \"$CN\" \"$PW\" | python3 -c \\\n"
        f"    'import json,sys; a=sys.stdin.read().split(\"\\0\"); "
        f"print(json.dumps({{\"ldapCn\":a[0],\"password\":a[1]}}))' | \\\n"
        f"    curl -sS -X POST {rs_host}/skill-api/login "
        f"-H 'Content-Type: application/json' --data @-\n"
        f"  export SKILL_LDAP_TOKEN='<响应里的 data.token>'\n"
        f"方式 B - 自动登录 (密码只留当前会话, 绝不写入 profile/history/任何文件):\n"
        f"  export RS_LDAP_CN='<你的cn>'\n"
        f"  read -rs RS_LDAP_PASSWORD && export RS_LDAP_PASSWORD\n"
    )


def resolve_token(rs_env: str, rs_host: str,
                  token_env_var: str = "SKILL_LDAP_TOKEN",
                  now: Optional[float] = None) -> Tuple[str, str]:
    """按优先级解析 token, 返回 (token, source); source ∈ {"env","cache","auto-login"}。

      1. 显式环境变量 token_env_var (旧行为, 最高优先, 不写缓存)
      2. 本地缓存 (未过期; RS_LDAP_CN 已知时仅复用**同主体**缓存, 见 r6 P1 绑定)
      3. RS_LDAP_CN + RS_LDAP_PASSWORD 自动登录 → 写缓存 (含登录确认的 ldapCn)
    全部不可用 → ManualLoginRequired (str(e) 为降级指引)。
    """
    explicit = os.environ.get(token_env_var, "").strip()
    if explicit:
        return explicit, "env"
    # CN/密码原样使用 (不 strip 裁剪 —— LDAP 密码策略未证实禁止首尾空白,
    # 裁剪会静默改凭据); strip 仅用于「是否已设置」的空白判定。
    # (来源:CI code-review r4 建议 2)
    cn = os.environ.get(CN_ENV, "")
    password = os.environ.get(PASSWORD_ENV, "")
    # 主体绑定 (r6 P1): CN 已知时, 只复用同主体的缓存 token ——
    # 同机切换账号 (旧缓存是 A 的 token) 不会被误用, 会自动重登换取 B 的 token。
    expect_cn = cn if cn.strip() else None
    cached = load_cached_token(rs_env, now=now, expect_cn=expect_cn)
    if cached:
        return cached, "cache"
    if cn.strip() and password.strip():
        data = http_login(rs_env, rs_host, cn, password)
        # TTL 契约校验 (来源:CI code-review r8 建议 1): int() 裸转会在上游给出
        # 非数值字符串时抛 ValueError 穿透; 非有限正数 / 超过 30 天的荒谬值一律
        # 回落缺省 3 天 —— 乐观缓存偏长的代价由 401/101 单次重登重试兜底。
        try:
            ttl = int(data.get("expiresInSeconds") or TOKEN_TTL_DEFAULT)
        except (TypeError, ValueError):
            ttl = TOKEN_TTL_DEFAULT
        if not 0 < ttl <= TOKEN_TTL_DEFAULT * 10:
            ttl = TOKEN_TTL_DEFAULT
        # 绑定登录响应确认的主体 (优先服务端回显, 回落提交值)。
        confirmed_cn = data.get("ldapCn") or cn
        _save_cached_token(rs_env, data["token"], ttl, now=now,
                           ldap_cn=confirmed_cn)
        return data["token"], "auto-login"
    raise ManualLoginRequired(manual_login_instructions(rs_env, rs_host))


def reauth_after_401(rs_env: str, rs_host: str,
                     token_env_var: str = "SKILL_LDAP_TOKEN") -> Tuple[str, str]:
    """401 后调用: 失效缓存再 resolve。显式 env token 无法自动续, 直接降级指引。"""
    invalidate_cached_token(rs_env)
    return resolve_token(rs_env, rs_host, token_env_var)


if __name__ == "__main__":  # pragma: no cover - 手工排障入口
    _rs_env = sys.argv[1] if len(sys.argv) > 1 else "staging"
    _hosts = {"staging": "https://console-test.addx.live/api",
              "prod": "https://revenus-sharing-backend.addx.live"}
    try:
        _token, _src = resolve_token(_rs_env, _hosts[_rs_env])
        print(f"[ok] source={_src} token-present={bool(_token)}")  # 不打印 token 本体
    except (ManualLoginRequired, RuntimeError) as e:
        sys.stderr.write(str(e))
        sys.exit(EXIT_MANUAL_LOGIN)
