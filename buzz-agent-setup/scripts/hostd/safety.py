"""Shared fd credential boundary and human, non-sensitive failure notices."""
from __future__ import annotations

import os
import stat
from pathlib import Path


class SafetyError(ValueError):
    """A local credential/configuration failed its safety contract."""


_NOTICES = {
    "file": ("配置或凭据文件无法安全读取，文件必须归当前用户所有、是普通文件且权限恰为 0600。", "请检查文件及目录是否为软链接，并由文件所有者修复权限后重试。", "帮我检查 hostd 配置和凭据文件的所有者、普通文件类型、0600 权限及软链接；不要显示文件内容。"),
    "permissions": ("配置文件权限不是 0600，跳过。", "请由文件所有者把配置权限改为 0600，并检查软链接后重新加载。", "帮我安全修复 hostd 配置文件的所有者和 0600 权限，不要输出凭据。"),
    "config": ("配置内容不完整或无法读取，已跳过这个绑定。", "请检查配置格式和必填字段，再重新加载这个绑定。", "帮我检查 hostd 绑定配置的格式和必填字段，不要输出凭据或消息正文。"),
    "chat": ("还没有绑定飞书群（config 里没有 chat_id）。", "请先完成群与频道绑定，再重新加载。", "帮我检查 hostd 的群与频道绑定是否已完成。"),
    "bot": ("没有配置同步 bot（desk_pubkey）。", "请为绑定配置本机同步 bot，再重新加载。", "帮我检查 hostd 的同步 bot 配置是否完整。"),
    "duplicate": ("同一群、频道或应用凭据存在冲突，相关绑定都已跳过。", "请确认每个群和频道只有一个绑定，同一应用使用同一份本机凭据配置。", "帮我排查 hostd 的重复群、重复频道及同一应用的凭据目录冲突；不要读取或输出密钥。"),
    "profile": ("这个 lark-cli 目录里没有这个飞书应用，hostd 不会用别人的凭据。", "请确认应用与本机凭据目录对应，并重新加载。", "帮我检查 hostd 飞书应用与本机 lark-cli 凭据目录的对应关系，不要输出凭据。"),
    "secret": ("应用凭据无法安全解密，这个应用暂时无法连接。", "请检查本机凭据文件权限和完整性，必要时重新配置应用凭据。", "帮我修复 hostd 本机飞书应用凭据的权限或完整性，不要输出密钥或应用 secret。"),
    "relay_config": ("relay 地址或认证配置不符合安全要求，这个绑定暂时无法连接。", "请使用已批准的安全 relay 地址；开发连接仅使用本机回环地址，并检查镜像凭据文件。", "帮我检查 hostd 的可信 relay 地址、加密连接和镜像凭据权限；不要输出凭据。"),
    "auth": ("relay 认证未通过，这个绑定暂时无法同步。", "请检查镜像身份与频道授权，然后重新连接。", "帮我排查 hostd 镜像身份的 relay 认证及频道授权，不要输出密钥或认证内容。"),
    "frame": ("relay 返回的消息格式无效，已忽略这条消息。", "若持续出现，请检查 relay 与 hostd 的协议是否兼容。", "帮我检查 hostd 与 relay 的协议兼容性，不要输出消息正文或认证内容。"),
    "closed": ("relay 关闭了这个绑定的订阅，正在重新连接。", "若反复断开，请检查频道授权及 relay 连接状态。", "帮我排查 hostd 的 relay 订阅关闭和频道授权，不要输出密钥或消息正文。"),
    "reconnect": ("这个绑定的 relay 连接中断，正在重试并补上遗漏消息。", "若持续无法恢复，请检查网络、relay 服务和频道授权。", "帮我排查 hostd 的 relay 连接中断并确认补洞状态，不要输出凭据或消息正文。"),
}


def notice(kind: str) -> str:
    """Fixed templates only: raw exceptions and peer text never enter status/logs."""
    reason, remedy, prompt = _NOTICES.get(kind, _NOTICES["config"])
    return f"{reason}\n怎么解决：{remedy}\n复制给 AI：{prompt}"


def read_owned(path: str | Path, *, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    """Read validated FD without reopening. Walk pinned nofollow directory FDs.

    Path replacement after validation cannot substitute another file. O_NONBLOCK
    prevents an unexpected FIFO/device from hanging before its type is checked.
    """
    path = Path(path)
    if ".." in path.parts or not path.name:
        raise SafetyError(notice("file"))
    if not path.is_absolute():
        path = Path.cwd() / path
    directory = fd = None
    try:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_size > max_bytes):
            raise SafetyError(notice("file"))
        chunks, length = [], 0
        while True:
            chunk = os.read(fd, min(65536, max_bytes + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > max_bytes:
                raise SafetyError(notice("file"))
        final = os.fstat(fd)
        if (final.st_uid != meta.st_uid or final.st_mode != meta.st_mode
                or final.st_size != meta.st_size or final.st_mtime_ns != meta.st_mtime_ns):
            raise SafetyError(notice("file"))
        return b"".join(chunks)
    except (OSError, ValueError):
        raise SafetyError(notice("file")) from None
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)
