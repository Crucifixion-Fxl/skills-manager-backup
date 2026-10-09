"""Durable, owner-only recovery deployment files; never runtime state.

Internal API, not an installer CLI. The caller must quiesce the recovery units
before publication and prove real manager/runtime health separately. A committed
file transaction alone never reports the feature installed.
"""
import base64
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from audit_local_alignment import trusted_directory_chain
from recovery_controller import _json


LIMIT = 128 * 1024
TARGETS = {
    "config": (".config/buzz/recovery/config.json", 0o600),
    "service": (".config/systemd/user/buzz-agent-recovery.service", 0o644),
    "timer": (".config/systemd/user/buzz-agent-recovery.timer", 0o644),
}
MANAGER_STEPS = ["quiesced", "reloaded", "tick_completed", "timer_enabled", "post_audit_passed"]
TERMINAL = {"committed", "rolled_back"}
# The runtime-start fence: from these phases the new controller may have
# processed work, so only forward completion is allowed, never file rollback.
FORWARD = {"runtime_started", "forward_fix_required"}
# Recorded manager steps each durable phase may carry (min, max).
PHASE_STEPS = {
    "prepared": (0, 1), "publishing": (1, 1), "published": (1, 2),
    "runtime_started": (2, 5), "forward_fix_required": (2, 5),
    "rolling_back": (0, 2), "rollback_blocked": (0, 2), "rolled_back": (0, 2), "committed": (5, 5),
}
BASELINE = "before-audit.json"
BASELINE_LIMIT = 16 * 1024 * 1024
MANAGER_BEFORE = {"service_present", "timer_present", "timer_was_enabled", "timer_was_active"}
REMEDIATION = {
    "installation_invalid": "安装文件或路径校验失败；请核对本机发布配置和文件权限，未完成安装。",
    "installation_busy": "另一个安装进程正在操作恢复服务；请等待它完成后重试。",
    "installation_interrupted": "发现未完成或无法核对的安装记录；请先核对备份与服务状态，不能覆盖后重试。",
    "installation_write_failed": "安装文件写入失败；请核对磁盘空间和权限，并检查安装记录中的回滚结果。",
    "installation_rollback_conflict": "安装期间文件被其他操作修改；已保留现场和备份，请核对冲突后修复。",
    "installation_rollback_unverified": "安装未完成，文件回滚也未能确认；已保留备份，请先修复磁盘或权限并核对服务状态，不要直接重试安装。",
    "installation_runtime_unverified": "新恢复服务可能已经处理任务，但健康检查未通过；已保留新配置和任务队列，请向前修复，不能直接退回旧版本。",
    "installation_repair_unverified": "安装记录无法唯一核对（缺记录、多条未完成或内容损坏）；未修改任何文件或服务。请保留安装目录，人工核对备份与服务状态后再处理。",
    "installation_repair_runtime_active": "恢复服务或控制器此刻仍在运行；未修改任何文件或服务。请等待本次运行结束后重新执行 --repair。",
    "installation_repair_runtime_evidence": "安装记录显示新服务尚未启动，但已有新配置的运行回执；新服务可能已处理任务，不能回滚。未修改任何文件或服务，请人工核对回执与任务队列后向前修复。",
    "installation_repair_baseline_missing": "缺少安装前的拓扑基线，无法证明向前完成后没有其它增减；未修改任何文件或服务。请人工对比升级前后审计回执后再处理。",
}


class InstallationFileError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def public(self):
        return dict(error=self.code, remediation=REMEDIATION[self.code], installed=False)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _directory(path, *, private=False, create=True):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        _directory(path.parent)
        path.mkdir(mode=0o700)
        _sync_directory(path.parent)
        info = path.lstat()
    if (not trusted_directory_chain(path, os.geteuid())
            or private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700)):
        raise InstallationFileError("installation_invalid")


def _snapshot(path, modes, *, limit=LIMIT):
    if not trusted_directory_chain(path.parent, os.geteuid()):
        raise InstallationFileError("installation_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) not in modes
                or before.st_size > limit):
            raise InstallationFileError("installation_invalid")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        linked = path.lstat()
    if (len(data) > limit or len(data) != before.st_size
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (before.st_dev, before.st_ino) != (linked.st_dev, linked.st_ino)):
        raise InstallationFileError("installation_invalid")
    return dict(data=base64.b64encode(data).decode("ascii"), mode=stat.S_IMODE(before.st_mode))


def _atomic(path, image, *, limit=2 * 1024 * 1024):
    """One same-directory replace, including directory durability and readback."""
    _directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(base64.b64decode(image["data"], validate=True))
            os.fchmod(stream.fileno(), image["mode"])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
        if _snapshot(path, {image["mode"]}, limit=limit) != image:
            raise InstallationFileError("installation_write_failed")
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_manager(manager):
    if manager is None:
        return
    if (not isinstance(manager, dict) or set(manager) != {"before", "steps"}
            or not isinstance(manager["before"], dict) or set(manager["before"]) != MANAGER_BEFORE
            or any(type(value) is not bool for value in manager["before"].values())
            or not isinstance(manager["steps"], list) or len(manager["steps"]) > len(MANAGER_STEPS)
            or manager["steps"] != MANAGER_STEPS[:len(manager["steps"])]) :
        raise ValueError("invalid manager installation record")
    before = manager["before"]
    if not before["timer_present"] and (before["timer_was_enabled"] or before["timer_was_active"]):
        raise ValueError("absent timer cannot be enabled or active")


def _validate_record(record, operation, phases):
    if (not isinstance(record, dict) or set(record) != {"version", "operation", "phase", "files", "manager"}
            or type(record["version"]) is not int or record["version"] != 2
            or record["operation"] != operation or record["phase"] not in phases
            or not isinstance(record["files"], dict) or set(record["files"]) != set(TARGETS)):
        raise ValueError("invalid or unfinished installation record")
    _validate_manager(record["manager"])
    if record["manager"] is not None:
        low, high = PHASE_STEPS[record["phase"]]
        if not low <= len(record["manager"]["steps"]) <= high:
            raise ValueError("incomplete or impossible manager installation result")
    for name, images in record["files"].items():
        if not isinstance(images, dict) or set(images) != {"before", "after"}:
            raise ValueError("invalid installation backup")
        for when, image in images.items():
            if when == "before" and image is None:
                continue
            modes = {TARGETS[name][1]} if when == "after" else ({0o600} if name == "config" else {0o600, 0o644})
            if (not isinstance(image, dict) or set(image) != {"data", "mode"}
                    or type(image["mode"]) is not int or image["mode"] not in modes
                    or not isinstance(image["data"], str) or len(image["data"]) > 4 * ((LIMIT + 2) // 3)):
                raise ValueError("invalid installation file image")
            data = base64.b64decode(image["data"], validate=True)
            if len(data) > LIMIT or base64.b64encode(data).decode("ascii") != image["data"]:
                raise ValueError("invalid installation file encoding")


def _validate_terminal_record(record, operation):
    _validate_record(record, operation, TERMINAL)


def _scan(root):
    """Every installation record under the root; any unknown entry is an error."""
    records = {}
    with os.scandir(root) as entries:
        for count, entry in enumerate(entries):
            if count >= 128:
                raise ValueError("installation history capacity")
            if entry.name == ".lock":
                continue
            if uuid.UUID(hex=entry.name).hex != entry.name:
                raise ValueError("unknown installation entry")
            path = root / entry.name
            _directory(path, private=True, create=False)
            image = _snapshot(path / "journal.json", {0o600}, limit=2 * 1024 * 1024)
            if image is None:
                raise ValueError("installation without a durable journal")
            record = _json(base64.b64decode(image["data"], validate=True))
            _validate_record(record, entry.name, set(PHASE_STEPS))
            records[entry.name] = (record, image)
    return records


def installation_status(home):
    """Read-only journal state for --check: clean, busy or needs_repair.

    Never creates the root or lock file, and only takes a shared,
    non-blocking lock probe on an existing lock file.
    """
    root = Path(home) / ".local/state/buzz-recovery/install"
    try:
        root.lstat()
    except FileNotFoundError:
        return "clean"
    lock = None
    try:
        try:
            lock = os.open(root / ".lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except FileNotFoundError:
            pass
        except BlockingIOError:
            return "busy"
        _directory(root, private=True, create=False)
        records = _scan(root)
        return "needs_repair" if any(record["phase"] not in TERMINAL for record, _ in records.values()) else "clean"
    except (OSError, ValueError, TypeError, KeyError, InstallationFileError):
        return "needs_repair"
    finally:
        if lock is not None:
            os.close(lock)


class InstallationFiles:
    def __init__(self, home, new, *, rollback_manager=None):
        if rollback_manager is not None and not callable(rollback_manager):
            raise InstallationFileError("installation_invalid")
        self.rollback_manager = rollback_manager
        self.home = Path(home)
        if (not self.home.is_absolute() or self.home.resolve(strict=True) != self.home
                or not trusted_directory_chain(self.home, os.geteuid())
                or not isinstance(new, dict) or set(new) != set(TARGETS)
                or any(type(value) is not bytes or len(value) > LIMIT for value in new.values())):
            raise InstallationFileError("installation_invalid")
        self.targets = {name: self.home / value[0] for name, value in TARGETS.items()}
        self.new = {name: dict(data=base64.b64encode(new[name]).decode("ascii"), mode=mode)
                    for name, (_, mode) in TARGETS.items()}
        self.root = self.home / ".local/state/buzz-recovery/install"
        self.lock_fd = None
        self.record = None
        self.journal = None
        self.journal_image = None
        self.adopted = False

    @classmethod
    def adopt(cls, home, *, rollback_manager):
        """Lock and take over the single unfinished record; None when none exists.

        Never creates installation state. The adopted transaction continues
        with the same exit rules: forward phases are never rolled back.
        """
        transaction = cls(home, {name: b"" for name in TARGETS}, rollback_manager=rollback_manager)
        try:
            transaction.root.lstat()
        except FileNotFoundError:
            return None
        transaction._lock()
        try:
            unfinished = [(operation, value) for operation, value in _scan(transaction.root).items()
                          if value[0]["phase"] not in TERMINAL]
            if not unfinished:
                transaction._release()
                return None
            if len(unfinished) != 1:
                raise ValueError("more than one unfinished installation")
            operation, (record, image) = unfinished[0]
            transaction.new = {name: record["files"][name]["after"] for name in TARGETS}
            transaction.record, transaction.journal_image = record, image
            transaction.journal = transaction.root / operation / "journal.json"
            transaction.adopted = True
            return transaction
        except (OSError, ValueError, TypeError, KeyError) as error:
            transaction._release()
            raise InstallationFileError("installation_repair_unverified") from error

    def release(self):
        """Give up an adopted record untouched (refusal before any change)."""
        self._release()

    def file_matches(self):
        """(every target is its backup or its new image, every target is new)."""
        known, published = True, True
        for name, path in self.targets.items():
            image = _snapshot(path, {0o600} if name == "config" else {0o600, 0o644})
            known &= image in (self.record["files"][name]["before"], self.record["files"][name]["after"])
            published &= image == self.record["files"][name]["after"]
        return known, published

    @property
    def baseline_path(self):
        return self.journal.parent / BASELINE

    def baseline(self, data):
        """Durable pre-install audit, before any manager mutation, for forward repair."""
        self._require("prepared")
        if self.record["manager"] is not None or type(data) is not bytes or len(data) > BASELINE_LIMIT:
            raise InstallationFileError("installation_invalid")
        self._assert_journal()
        _atomic(self.baseline_path, dict(data=base64.b64encode(data).decode("ascii"), mode=0o600),
                limit=BASELINE_LIMIT)

    def read_baseline(self):
        image = _snapshot(self.baseline_path, {0o600}, limit=BASELINE_LIMIT)
        if image is None:
            raise ValueError("missing installation baseline")
        return _json(base64.b64decode(image["data"], validate=True))

    def resume_forward(self):
        """Re-enter the runtime fence to complete an adopted forward-only record."""
        self._require(*FORWARD)
        if self.record["manager"] is None:
            raise InstallationFileError("installation_invalid")
        self._phase("runtime_started")

    def _release(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def _lock(self):
        _directory(self.root, private=True)
        self.lock_fd = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                               | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
        info = os.fstat(self.lock_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 0):
            raise InstallationFileError("installation_invalid")
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallationFileError("installation_busy") from error
        _sync_directory(self.root)

    def _check_history(self):
        try:
            if any(record["phase"] not in TERMINAL for record, _image in _scan(self.root).values()):
                raise ValueError("unfinished installation")
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise InstallationFileError("installation_interrupted") from error

    def _assert_journal(self):
        try:
            if _snapshot(self.journal, {0o600}, limit=2 * 1024 * 1024) != self.journal_image:
                raise ValueError("installation journal changed")
        except (OSError, ValueError) as error:
            raise InstallationFileError("installation_interrupted") from error

    def _phase(self, phase, **changes):
        self._assert_journal()
        record = {**self.record, "phase": phase, **changes}
        image = dict(data=base64.b64encode(json.dumps(record, sort_keys=True).encode()).decode("ascii"), mode=0o600)
        _atomic(self.journal, image)
        self.record = record
        self.journal_image = image

    def __enter__(self):
        if self.adopted and self.lock_fd is not None:
            return self
        if self.lock_fd is not None or self.record is not None:
            raise InstallationFileError("installation_invalid")
        try:
            self._lock()
            self._check_history()
            files = {}
            for name, path in self.targets.items():
                _directory(path.parent)
                files[name] = dict(before=_snapshot(path, {0o600} if name == "config" else {0o600, 0o644}),
                                   after=self.new[name])
            operation = uuid.uuid4().hex
            directory = self.root / operation
            directory.mkdir(mode=0o700)
            _sync_directory(self.root)
            self.journal = directory / "journal.json"
            self.record = dict(version=2, operation=operation, phase="preparing", files=files, manager=None)
            self._phase("prepared")
            return self
        except (OSError, ValueError) as error:
            self._release()
            if isinstance(error, InstallationFileError):
                raise
            raise InstallationFileError("installation_invalid") from error

    def _require(self, *phases):
        if self.lock_fd is None or self.record is None or self.record["phase"] not in phases:
            raise InstallationFileError("installation_invalid")

    def publish(self):
        self._require("prepared")
        if self.record["manager"] is not None and self.record["manager"]["steps"] != ["quiesced"]:
            raise InstallationFileError("installation_invalid")
        self._phase("publishing")
        for name, path in self.targets.items():
            if _snapshot(path, {0o600} if name == "config" else {0o600, 0o644}) != self.record["files"][name]["before"]:
                raise InstallationFileError("installation_rollback_conflict")
            _atomic(path, self.new[name])
        self._phase("published")

    def manager_before(self, previous):
        """Durable, immutable baseline before the first manager mutation."""
        self._require("prepared")
        try:
            manager = dict(before=previous, steps=[])
            _validate_manager(manager)
            if self.record["manager"] is not None or self.rollback_manager is None:
                raise ValueError("manager baseline already captured or cannot restore")
        except (ValueError, TypeError) as error:
            raise InstallationFileError("installation_invalid") from error
        self._phase("prepared", manager=dict(before=dict(previous), steps=[]))

    def manager_step(self, step):
        """Ordered verified checkpoints; never replaces the runtime-start fence."""
        self._require("prepared", "published", "runtime_started")
        manager = self.record["manager"]
        phases = {"quiesced": "prepared", "reloaded": "published",
                  **{name: "runtime_started" for name in MANAGER_STEPS[2:]}}
        if (manager is None or step not in phases or phases[step] != self.record["phase"]
                or manager["steps"] + [step] != MANAGER_STEPS[:len(manager["steps"]) + 1]):
            raise InstallationFileError("installation_invalid")
        self._phase(self.record["phase"], manager={**manager, "steps": manager["steps"] + [step]})

    def runtime_started(self):
        """Persist BEFORE asking the manager to start a potentially publishing tick."""
        self._require("published")
        if self.record["manager"] is not None and self.record["manager"]["steps"] != MANAGER_STEPS[:2]:
            raise InstallationFileError("installation_invalid")
        self._phase("runtime_started")

    def commit(self):
        self._require("published", "runtime_started")
        if self.record["manager"] is not None and self.record["manager"]["steps"] != MANAGER_STEPS:
            raise InstallationFileError("installation_invalid")
        for name, path in self.targets.items():
            if _snapshot(path, {TARGETS[name][1]}) != self.new[name]:
                raise InstallationFileError("installation_rollback_conflict")
        self._phase("committed")
        return dict(files_published=True, installed=False)

    def _rollback(self):
        # Check EVERY target before restoring any of them. A concurrent owner
        # edit cannot be overwritten or hidden by a partially restored config.
        try:
            images = {name: _snapshot(path, {0o600} if name == "config" else {0o600, 0o644})
                      for name, path in self.targets.items()}
            if any(images[name] not in (item["before"], item["after"])
                   for name, item in self.record["files"].items()):
                raise InstallationFileError("installation_rollback_conflict")
        except (OSError, ValueError) as error:
            try:
                self._phase("rollback_blocked")
            except (OSError, ValueError):
                pass  # Earlier nonterminal journal still blocks another install.
            raise InstallationFileError("installation_rollback_conflict") from error
        try:
            self._phase("rolling_back")
            for name, path in self.targets.items():
                before = self.record["files"][name]["before"]
                if images[name] == before:
                    continue
                if before is None:
                    path.unlink()
                    _sync_directory(path.parent)
                else:
                    _atomic(path, before)
            if self.record["manager"] is not None:
                self.rollback_manager(dict(self.record["manager"]["before"]))
            self._phase("rolled_back")
        except (OSError, ValueError) as error:
            raise InstallationFileError("installation_rollback_unverified") from error

    def __exit__(self, kind, error, traceback):
        try:
            self._assert_journal()
            if self.record["phase"] == "committed":
                return False
            if self.record["phase"] in FORWARD:
                try:
                    self._phase("forward_fix_required")
                except (OSError, ValueError):
                    pass  # Never touch files/runtime state after this fence.
                raise InstallationFileError("installation_runtime_unverified") from error
            self._rollback()
            if error is not None:
                if isinstance(error, InstallationFileError):
                    raise error
                raise InstallationFileError("installation_write_failed") from error
        finally:
            self._release()
