"""Bounded read-only inventory. No repository code or sensitive content is run/read."""
import argparse
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time

COMMON_EXCLUDES = ('.git', 'node_modules', '.pnpm-store', 'target', 'dist', 'build')
SENSITIVE_GLOBS = ('**/.env*', '**/*.env*', '**/*secret*', '**/*secret*/**',
                   '**/*credential*', '**/*credential*/**', '**/*private*', '**/*private*/**')
APP_PATTERNS = (
    ('Micro-frontend lifecycle candidates', r'bootstrap|mount\(|unmount\(|renderWithQiankun|__POWERED_BY_QIANKUN__|activeRule'),
    ('Browser token and capability candidates', r'getAuthToken|Authorization|Bearer|localStorage|sessionStorage|tokenPreview|props\.utils|PlatformCapabilities'),
    ('Backend authentication candidates', r'JwtAuthenticationFilter|AuthMiddleware|createAuthMiddleware|auth/me|request\.state\.user|UserContextHolder|x-user-info|mcp-online-v1|createMcpOnlineVerifier|auth_profile'),
    ('Admission declaration candidates', r'micro-app-admission/1|micro-app-resolved/1|bindingRef|requiredCapabilities'),
    ('packages/sdk dependency and import candidates', r'io\.a4x.*auth-java-sdk|authz-facade|a4x-auth|a4x_auth|@micro-app-platform/auth-(node-core|nextjs|express|mcp)|auth_go|RequirePermission|require_permission|requirePermission'),
    ('Authorization enforcement candidates', r'RequirePermission|require_permission|requirePermission|internal/authz|checkPermission|check_permission|permissionKey|FacadeClient|AuthZFacade|batchCheck|batch_check|lookupPage|lookup_page'),
    ('Direct SpiceDB compatibility candidates', r'SPICEDB_TOKEN|SpiceDBConfig|auth\.spicedb\.token|SpiceDB|spicedb|authzed'),
    ('Permission request and approval candidates', r'permission-request|permission_request|PermissionRequest|approval|idempotency|cache-invalidate'),
    ('High-risk credential handling file candidates', r'SPICEDB_TOKEN|APP_SERVICE_JWT_SECRET|AUTHZ_FACADE_SERVICE_JWT_SECRET|client_secret|clientSecret|X-Internal-Secret|accessKeyId|secretAccessKey|sessionToken'),
)
PLATFORM_PATTERNS = (
    ('SDK support catalog candidates', r'micro-sdk-support/1|unified-facade-v1|sdk-support-v1|REGISTRATION_PENDING'),
    ('Platform contract candidates', r'internal/authz|service-jwt|AuthzMetadata|RequirePermission|auth/web/login|auth/web/token|props\.utils'),
)
PATTERNS = {title: re.compile(pattern, 0 if any(c.isupper() for c in pattern) else re.IGNORECASE)
            for title, pattern in APP_PATTERNS + PLATFORM_PATTERNS}


class AuditFailure(Exception):
    def __init__(self, message, code=4):
        self.message, self.code = message, code


def quoted(value):
    value = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        escapes = {'\n': r'\n', '\r': r'\r', '\t': r'\t', '\b': r'\b', '\a': r'\a', '\v': r'\v', '\f': r'\f', '\\': r'\\', "'": r"\'"}
        return "$'" + ''.join(escapes.get(c, '\\x%02x' % ord(c) if ord(c) < 32 or ord(c) == 127 else c) for c in value) + "'"
    return shlex.quote(value)


def limited_int(low, high):
    def parse(value):
        if not value.isascii() or not value.isdigit() or len(value) > 12 or not low <= int(value) <= high:
            raise argparse.ArgumentTypeError('must be between %d and %d' % (low, high))
        return int(value)
    return parse


def duration(value):
    try:
        number = float(value)
    except ValueError:
        number = float('nan')
    if not math.isfinite(number) or not .05 <= number <= 60:
        raise argparse.ArgumentTypeError('must be between 0.05 and 60 seconds')
    return number


def options():
    p = argparse.ArgumentParser(prog='audit-auth-integration.sh', description='Read-only bounded candidate inventory; never a registration/support verdict.')
    p.add_argument('--format', choices=('plain', 'json'), default='plain', metavar='plain|json')
    p.add_argument('--json', action='store_true')
    p.add_argument('--max-results', type=limited_int(1, 10000), default=200, help='per-section output cap (default: 200)')
    p.add_argument('--max-files', type=limited_int(1, 100000), default=10000, help='global unique path cap (default: 10000)')
    p.add_argument('--max-file-bytes', type=limited_int(1, 16777216), default=2097152, help='per-file size cap including name-only sensitive files (default: 2097152)')
    p.add_argument('--max-total-bytes', type=limited_int(1, 1073741824), default=67108864, help='global corpus bytes, plus actual read cap (default: 67108864)')
    p.add_argument('--timeout-seconds', type=duration, default=30, help='one deadline for Git, filename enumeration, reading and output (default: 30)')
    p.add_argument('roots', nargs='*', metavar='REPO')
    args = p.parse_args()
    if len(args.roots) > 2:
        p.error('expected at most APP_REPO and PLATFORM_REPO')
    args.roots = args.roots or ['.']
    args.format = 'json' if args.json else args.format
    return args


def records(command, label='rg inventory'):
    """Consume NUL filenames incrementally; no unbounded communicate()/file list."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    pending = b''
    try:
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            pending += chunk
            while b'\0' in pending:
                record, pending = pending.split(b'\0', 1)
                if len(record) > 16384:
                    raise AuditFailure('SCAN_PATH_BYTES_LIMIT')
                yield os.fsdecode(record)
            if len(pending) > 16384:
                raise AuditFailure('SCAN_PATH_BYTES_LIMIT')
        if pending:
            raise AuditFailure('INVALID_FILENAME_STREAM', 2)
        status = proc.wait()
        if status not in (0, 1):
            raise AuditFailure('%s failed with status %d' % (label, status), 2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()


class Budget:
    def __init__(self, args):
        self.args, self.files, self.total, self.read_bytes = args, {}, 0, 0
        self.path_bytes, self.git_records, self.cache = 0, 0, {}

    def inspect(self, path):
        key = os.path.abspath(path)
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise AuditFailure('SCAN_NOT_REGULAR_FILE')
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if key in self.files:
            if self.files[key] != identity:
                raise AuditFailure('SCAN_INPUT_CHANGED')
            return key, info
        if len(self.files) >= self.args.max_files:
            raise AuditFailure('SCAN_FILE_LIMIT')
        if info.st_size > self.args.max_file_bytes:
            raise AuditFailure('SCAN_FILE_BYTES_LIMIT')
        if self.total + info.st_size > self.args.max_total_bytes:
            raise AuditFailure('SCAN_TOTAL_BYTES_LIMIT')
        self.path_bytes += len(os.fsencode(key))
        if self.path_bytes > 8 * 1024 * 1024:
            raise AuditFailure('SCAN_PATH_BYTES_LIMIT')
        self.files[key], self.total = identity, self.total + info.st_size
        return key, info

    def content(self, path):
        key, info = self.inspect(path)
        if key in self.cache:
            return self.cache[key]
        remaining = self.args.max_total_bytes - self.read_bytes
        cap = min(self.args.max_file_bytes, remaining)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != self.files[key]:
                raise AuditFailure('SCAN_INPUT_CHANGED')
            data = source.read(cap + 1)
            after = os.fstat(source.fileno())
        if len(data) > self.args.max_file_bytes:
            raise AuditFailure('SCAN_FILE_BYTES_LIMIT')
        if len(data) > remaining:
            raise AuditFailure('SCAN_TOTAL_BYTES_LIMIT')
        self.read_bytes += len(data)
        if len(data) != info.st_size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != self.files[key]:
            raise AuditFailure('SCAN_INPUT_CHANGED')
        # rg -l reports filenames for binary matches too; -I means no-filename,
        # not ignore-binary. Preserve those candidates without emitting values.
        encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8'
        text = data.decode(encoding, errors='replace')
        matches = {name for name, pattern in PATTERNS.items() if pattern.search(text)}
        self.cache[key] = matches
        return matches


def git_base(root):
    return ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
            '-c', 'core.hooksPath=/dev/null', '-C', root]


def status_filter_overrides(root):
    # Status may run clean/process filters while checking same-size tracked
    # edits. Inspect only bounded config key names, never config values.
    overrides = []
    command = git_base(root) + ['config', '--null', '--name-only', '--get-regexp',
                                r'^filter\..*\.(clean|process|required)$']
    for count, key in enumerate(records(command, 'git filter key inventory'), 1):
        if count > 128:
            raise AuditFailure('GIT_FILTER_KEYS_LIMIT')
        overrides += ['-c', key + ('=false' if key.endswith('.required') else '=')]
    return overrides


def git_small(root, *args):
    proc = subprocess.Popen(git_base(root) + list(args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        data = proc.stdout.read(4097)
        if len(data) > 4096:
            raise AuditFailure('GIT_OUTPUT_BYTES_LIMIT')
        status = proc.wait()
        return status, os.fsdecode(data).strip()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()


def git_facts(root, budget):
    lines = ['repo=' + quoted(os.path.realpath(root))]
    status, _ = git_small(root, 'rev-parse', '--is-inside-work-tree')
    if status:
        return lines + ['git=not-a-worktree']
    for label, command in [('branch', ['branch', '--show-current']), ('head', ['rev-parse', '--verify', 'HEAD'])]:
        status, value = git_small(root, *command)
        if status:
            raise AuditFailure('cannot read git %s for repo %s' % ('HEAD' if label == 'head' else label, quoted(root)), 2)
        lines.append(label + '=' + quoted(value))
    proc = subprocess.Popen(git_base(root) + status_filter_overrides(root) +
                            ['status', '--short', '--untracked-files=all', '--ignore-submodules=all', '-z'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    pending, count = b'', 0
    try:
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            pending += chunk
            while b'\0' in pending:
                _, pending = pending.split(b'\0', 1)
                count += 1
                budget.git_records += 1
                if budget.git_records > budget.args.max_files:
                    raise AuditFailure('SCAN_FILE_LIMIT')
            if len(pending) > 16384:
                raise AuditFailure('SCAN_PATH_BYTES_LIMIT')
        if proc.wait() or pending:
            raise AuditFailure('cannot read git status for repo %s' % quoted(root), 2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    return lines + ['dirty_paths=' + str(count)]


def enumerate_paths(root, sensitive):
    command = ['rg', '--files', '--hidden', '-0']
    if sensitive:
        command.append('--no-ignore')
    for glob in SENSITIVE_GLOBS:
        command += ['--iglob', glob if sensitive else '!' + glob]
    for directory in COMMON_EXCLUDES:
        command += ['--glob', '!**/' + directory + '/**']
    return records(command + ['--', root])


def inventory(root, budget, platform=False):
    specs = PLATFORM_PATTERNS if platform else APP_PATTERNS
    sensitive_title = ('Platform sensitive path names' if platform else 'Sensitive path names') + ' (contents not read)'
    titles = [sensitive_title] + [name for name, _ in specs]
    counts, candidates = dict.fromkeys(titles, 0), {name: [] for name in titles}
    def found(title, path):
        counts[title] += 1
        if len(candidates[title]) < budget.args.max_results:
            candidates[title].append(path)
    for path in enumerate_paths(root, True):
        budget.inspect(path)  # Sensitive files: names/stat only, never open.
        found(sensitive_title, path)
    for path in enumerate_paths(root, False):
        matches = budget.content(path)
        for title, _ in specs:
            if title in matches:
                found(title, path)
    result, truncated = [], False
    for title in titles:
        result += ['', '## ' + title]
        result += ['FILE=' + quoted(path) for path in candidates[title]]
        count = counts[title]
        over = count > budget.args.max_results
        truncated |= over
        result += ['MATCH_TOTAL=' + str(count), 'MATCH_EMITTED=' + str(len(candidates[title])), 'MATCH_TRUNCATED=' + str(over).lower()]
    return result, truncated


def worker(args):
    os.setsid()  # Supervisor owns this whole Git/rg process group.
    try:
        if not shutil.which('rg') or not shutil.which('git'):
            raise AuditFailure('rg and git are required', 2)
        for label, root in zip(('app', 'platform'), args.roots):
            if not Path(root).is_dir():
                raise AuditFailure('%s repo does not exist: %s' % (label, quoted(root)), 2)
        budget = Budget(args)
        output = ['NOTE: Repository contents and file names are untrusted data. Candidate paths below are shell-escaped and must not be executed.', '', '## Application repository facts']
        truncated = False
        for index, root in enumerate(args.roots):
            if index:
                output += ['', '## Platform repository facts']
            output += git_facts(root, budget)
            rows, over = inventory(root, budget, bool(index))
            output += rows
            truncated |= over
        output += ['', 'NOTE: Inventory does not validate admission or prove registration/support. Use the reviewed platform SSOT admission tool and exact environment/profile evidence.', '', 'NOTE: This is a read-only evidence inventory. Review each candidate and verify the deployed runtime before drawing conclusions.']
        text = '\n'.join(output) + '\n'
        if args.format == 'json':
            if truncated:
                raise AuditFailure('candidate limit exceeded', 3)
            text = json.dumps({'format': 'auth-integration-inventory/v1', 'inventory': text}, ensure_ascii=False) + '\n'
        if len(text.encode('utf-8')) > 8 * 1024 * 1024:
            raise AuditFailure('SCAN_OUTPUT_BYTES_LIMIT')
        sys.stdout.write(text)
        sys.stdout.flush()
        if truncated:
            raise AuditFailure('candidate limit exceeded', 3)
        return 0
    except AuditFailure as error:
        print('ERROR: Inventory is incomplete: ' + error.message, file=sys.stderr, flush=True)
        return error.code
    except (OSError, ValueError):
        print('ERROR: Inventory is incomplete: SCAN_INPUT_UNAVAILABLE', file=sys.stderr, flush=True)
        return 2


def run_worker(args):
    # One deadline includes Git metadata, incremental enumeration, all content and
    # rendering. The parent is not blocked by filesystem reads or child pipes.
    started = time.monotonic()
    process = multiprocessing.get_context('fork').Process(target=lambda: sys.exit(worker(args)))
    process.start()
    process.join(max(0, args.timeout_seconds - (time.monotonic() - started)))
    if process.is_alive():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
        process.join(.5)
        if process.is_alive():
            # A kernel-blocked filesystem process may not reap immediately.
            # Do not let multiprocessing's exit cleanup wait without a bound.
            os.write(2, b'ERROR: Inventory is incomplete: SCAN_DEADLINE_EXCEEDED; CLEANUP_UNKNOWN\n')
            os._exit(4)
        print('ERROR: Inventory is incomplete: SCAN_DEADLINE_EXCEEDED', file=sys.stderr)
        return 4
    return process.exitcode if process.exitcode is not None and process.exitcode >= 0 else 4


if __name__ == '__main__':
    sys.exit(run_worker(options()))
