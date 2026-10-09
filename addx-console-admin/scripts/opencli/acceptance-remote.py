"""Private-stdin remote OpenCLI read acceptance; never writes credentials."""
import ast, json, os, pathlib, re, shutil, subprocess, sys, tempfile

class PolicyRejected(Exception):
    pass

def _tokens(source):
    """Conservative literal-metadata lexer, never evaluates JavaScript."""
    if not isinstance(source, str):
        raise PolicyRejected()
    out, i = [], 0
    while i < len(source):
        c = source[i]
        if c.isspace():
            i += 1
            continue
        if source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end + 1
            continue
        if source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:
                raise PolicyRejected()
            i = end + 2
            continue
        if c in "\"'`":
            start, quote = i, c
            i += 1
            while i < len(source):
                if source[i] == '\\':
                    i += 2
                elif source[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
            else:
                raise PolicyRejected()
            raw = source[start:i]
            if quote == '`':
                # Registration hidden inside an interpolation is not supported.
                if '${' in raw and re.search(r'\bcli\s*\(', raw):
                    raise PolicyRejected()
                out.append(('template', raw))
            else:
                try:
                    value = ast.literal_eval(raw)
                except Exception:
                    raise PolicyRejected()
                out.append(('string', value))
            continue
        if c == '/' and (not out or out[-1][1] in ('(', '=', ':', ',', '[', '!', 'return')):
            i += 1
            in_class = False
            while i < len(source):
                if source[i] == '\\':
                    i += 2
                    continue
                if source[i] == '[':
                    in_class = True
                elif source[i] == ']':
                    in_class = False
                elif source[i] == '/' and not in_class:
                    i += 1
                    while i < len(source) and source[i].isalpha():
                        i += 1
                    out.append(('regex', 'regex'))
                    break
                i += 1
            else:
                raise PolicyRejected()
            continue
        match = re.match(r'[A-Za-z_$][\w$]*', source[i:])
        if match:
            value = match.group(0)
            out.append(('identifier', value))
            i += len(value)
        elif source.startswith('...', i):
            out.append(('punct', '...'))
            i += 3
        else:
            out.append(('punct', c))
            i += 1
    return out

def _metadata(source):
    tokens = _tokens(source)
    calls = [i for i, t in enumerate(tokens[:-1]) if t == ('identifier', 'cli') and tokens[i + 1][1] == '(']
    if len(calls) != 1:
        raise PolicyRejected()
    start = calls[0]
    if start and tokens[start - 1][1] == '.':
        raise PolicyRejected()
    if tokens[start + 2][1] != '{':
        raise PolicyRejected()
    stack, properties, metadata = ['{'], set(), {}
    i, key_position = start + 3, True
    matching = {')': '(', ']': '[', '}': '{'}
    while i < len(tokens):
        kind, value = tokens[i]
        if len(stack) == 1:
            if value == '...':
                raise PolicyRejected()
            if key_position and value != '}':
                if kind not in ('identifier', 'string') or i + 1 >= len(tokens) or tokens[i + 1][1] != ':':
                    raise PolicyRejected()
                if value in properties:
                    raise PolicyRejected()
                properties.add(value)
                if value in ('site', 'name', 'access'):
                    if i + 2 >= len(tokens) or tokens[i + 2][0] != 'string':
                        raise PolicyRejected()
                    metadata[value] = tokens[i + 2][1]
                key_position = False
            elif value == ',':
                key_position = True
        if kind == 'punct' and value in ('(', '[', '{'):
            stack.append(value)
        elif kind == 'punct' and value in matching:
            if not stack or stack.pop() != matching[value]:
                raise PolicyRejected()
            if not stack:
                if i + 1 >= len(tokens) or tokens[i + 1][1] != ')':
                    raise PolicyRejected()
                break
        i += 1
    else:
        raise PolicyRejected()
    if set(metadata) != {'site', 'name', 'access'} or metadata['site'] != 'addx-console' or metadata['access'] not in ('read', 'write'):
        raise PolicyRejected()
    return metadata

def _module_specifiers(source):
    """Literal ESM/CJS dependency inventory; no JavaScript evaluation.

    Computed imports cannot prove a self-contained acceptance bundle and fail
    closed. Bare packages remain the pinned runtime's responsibility.
    """
    tokens = _tokens(source)
    specs = []
    for i, (kind, value) in enumerate(tokens):
        if kind == 'template' and '${' in value and re.search(r'\b(?:import|require)\s*\(', value):
            raise PolicyRejected()
        if kind != 'identifier' or value not in ('import', 'export', 'require'):
            continue
        if i and tokens[i - 1][1] == '.':
            continue
        if i + 1 >= len(tokens):
            raise PolicyRejected()
        next_kind, next_value = tokens[i + 1]
        if value == 'import' and next_value == '.':  # import.meta
            continue
        if next_value == '(' and value in ('import', 'require'):
            if i + 3 >= len(tokens) or tokens[i + 2][0] != 'string' or tokens[i + 3][1] != ')':
                raise PolicyRejected()
            specs.append(tokens[i + 2][1])
            continue
        if value == 'require':
            continue
        if value == 'import' and next_kind == 'string':
            specs.append(next_value)
            continue
        if value == 'export' and next_value not in ('*', '{'):
            continue
        depth, found = 0, False
        for j in range(i + 1, len(tokens)):
            tk, tv = tokens[j]
            if tk == 'punct' and tv == '{':
                depth += 1
            elif tk == 'punct' and tv == '}':
                depth -= 1
            if depth == 0 and tk == 'identifier' and tv == 'from':
                if j + 1 >= len(tokens) or tokens[j + 1][0] != 'string':
                    raise PolicyRejected()
                specs.append(tokens[j + 1][1]); found = True
                break
            if depth == 0 and tv == ';':
                break
            # Local export {x} may end at the closing brace, without a semicolon.
            if value == 'export' and depth == 0 and tv == '}' and (j + 1 >= len(tokens) or tokens[j + 1] != ('identifier', 'from')):
                break
        if value == 'import' and not found:
            raise PolicyRejected()
    return specs


def _validate_import_closure(files):
    # Files are deliberately flat; extensions must be exact and namespace
    # traversal/file URLs never inherit a file from the host checkout.
    for source in files.values():
        for spec in _module_specifiers(source):
            if spec.startswith('.'):
                if not re.fullmatch(r'\./[A-Za-z0-9_-]+\.(?:js|mjs)', spec) or spec[2:] not in files:
                    raise PolicyRejected()
            elif spec.startswith(('/', '\\')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', spec) and not spec.startswith('node:'):
                raise PolicyRejected()


def validate_acceptance_policy(payload):
    """This read-only acceptance executor is not a general business runner."""
    files, commands = payload.get('files'), payload.get('commands')
    if not isinstance(files, dict) or not isinstance(commands, list):
        raise PolicyRejected()
    for name, source in files.items():
        if not isinstance(name, str) or pathlib.Path(name).name != name or not name.endswith(('.js', '.mjs')) or not isinstance(source, str):
            raise PolicyRejected()
    _validate_import_closure(files)
    for entry in commands:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise PolicyRejected()
        command, flags = entry
        if not isinstance(command, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', command) or not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
            raise PolicyRejected()
        candidates = [files[name] for name in (command + '.js', command + '.mjs') if name in files]
        if len(candidates) != 1:
            raise PolicyRejected()
        metadata = _metadata(candidates[0])
        if metadata['name'] != command:
            raise PolicyRejected()
        if metadata['access'] == 'write':
            modes = []
            for i, flag in enumerate(flags):
                if flag == '--mode':
                    if i + 1 >= len(flags):
                        raise PolicyRejected()
                    modes.append(flags[i + 1])
                elif flag.startswith('--mode='):
                    modes.append(flag.split('=', 1)[1])
                elif flag.startswith('--mode'):
                    raise PolicyRejected()
            if modes != ['dry-run'] or '--' in flags:
                raise PolicyRejected()


# OpenCLI 1.8.8 installed README and dist/src/errors.js: exit-code categories
# are coarse (75 also covers SESSION_BUSY; 77 can cover permission/login wall).
def classify_opencli_exit(code):
    if type(code) is not int:
        return 'EXECUTION_FAILED'
    return {0: 'SUCCESS', 2: 'ARGUMENT_ERROR', 66: 'EMPTY_RESULT',
            69: 'BROWSER_UNAVAILABLE', 75: 'TIMEOUT', 77: 'AUTH_REQUIRED',
            78: 'CONFIG_ERROR', 130: 'INTERRUPTED'}.get(code, 'EXECUTION_FAILED')


def cli_read_record(command, flags, result):
    category = classify_opencli_exit(result.returncode)
    # Never parse or expose stdout/stderr on a nonzero result.
    output = json.loads(result.stdout) if category == 'SUCCESS' else {'failed': True}
    return {'command': command, 'args': flags, 'exitCode': result.returncode,
            'typedExitCategory': category, 'output': output}


def reads_succeeded(reads, expected_count):
    return (type(expected_count) is int and expected_count > 0
            and isinstance(reads, list) and len(reads) == expected_count
            and all(isinstance(row, dict) and type(row.get('exitCode')) is int
                    and row['exitCode'] == 0 for row in reads))


def acceptance_results_summary(local, remote, expected_count):
    local_ok = reads_succeeded(local, expected_count)
    remote_ok = (isinstance(remote, dict) and remote.get('status') == 'FINISHED'
                 and reads_succeeded(remote.get('reads'), expected_count))
    return {'sameResult': isinstance(remote, dict) and local == remote.get('reads'),
            'localReadsSucceeded': local_ok, 'remoteReadsSucceeded': remote_ok,
            'allReadsSucceeded': local_ok and remote_ok}


def validate_acceptance_namespace(site):
    """Pure preflight; preserve the existing remote namespace predicate exactly."""
    if not isinstance(site, str) or not site.startswith('addx-console-acceptance-') or not all(c.isalnum() or c == '-' for c in site):
        raise PolicyRejected()
    return site


def run(payload):
    runtime = pathlib.Path(tempfile.mkdtemp(prefix='addx-opencli-', dir='/tmp'))
    site = payload.get('site', '')
    try:
        validate_acceptance_namespace(site)
    except PolicyRejected:
        shutil.rmtree(runtime)
        payload.pop('token', None)
        return {'status': 'INVALID_NAMESPACE', 'reads': [], 'readsSucceeded': False, 'cleanup': {'ownedAdapterRemoved': True, 'temporaryRuntimeRemoved': not runtime.exists(), 'preexistingAdapterExisted': None, 'preexistingAdapterPreserved': None, 'preexistingAdapterPreservationStatus': 'NOT_APPLICABLE', 'credentialFilesCreated': False}}
    folder = pathlib.Path.home() / '.opencli/clis' / site
    preexisting_adapter_existed = folder.exists() or folder.is_symlink()
    resolver = pathlib.Path.home() / '.opencli/node_modules/@jackwener/opencli'
    resolver_existed = resolver.exists() or resolver.is_symlink()
    created_folder = False
    env = os.environ.copy()
    env.pop('CONSOLE_TOKEN', None)
    env.pop('CONSOLE_ALLOW_WRITE', None)
    reads = []
    status = 'FAILED'
    try:
        validate_acceptance_policy(payload)
        if folder.exists() or folder.is_symlink():
            status = 'NAMESPACE_EXISTS'
            return_result = True
        else:
            return_result = False
            install = subprocess.run(['npm', 'install', '--prefix', str(runtime), '--ignore-scripts', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org', '@jackwener/opencli@1.8.8'], env=env, capture_output=True, text=True, timeout=50)
            if install.returncode:
                status = 'INSTALL_FAILED'
                return_result = True
        if not return_result:
            folder.mkdir(parents=True)
            created_folder = True
            for name, source in payload['files'].items():
                if pathlib.Path(name).name != name or not name.endswith(('.js', '.mjs')):
                    raise ValueError('Invalid adapter filename')
                (folder / name).write_text(source.replace("site:'addx-console'", "site:'" + site + "'"))
            env['CONSOLE_TOKEN'] = payload.pop('token')
            if payload.get('expectedEmail'):
                env['CONSOLE_EXPECTED_EMAIL'] = payload['expectedEmail']
            for command, flags in payload['commands']:
                result = subprocess.run([str(runtime / 'node_modules/.bin/opencli'), site, command, *flags, '--format', 'json'], env=env, capture_output=True, text=True, timeout=45)
                reads.append(cli_read_record(command, flags, result))
            status = 'FINISHED'
    except PolicyRejected:
        status = 'POLICY_REJECTED'
    except subprocess.TimeoutExpired:
        status = 'TIMEOUT'
    except Exception:
        status = 'EXECUTION_FAILED'
    finally:
        env.pop('CONSOLE_TOKEN', None)
        payload.pop('token', None)
        if created_folder:
            shutil.rmtree(folder, ignore_errors=True)
        if not resolver_existed and resolver.is_symlink() and str(resolver.resolve()).startswith(str(runtime) + '/'):
            resolver.unlink()
        shutil.rmtree(runtime, ignore_errors=True)
    return {'status': status, 'reads': reads, 'readsSucceeded': status == 'FINISHED' and reads_succeeded(reads, len(payload['commands']) if isinstance(payload.get('commands'), list) else 0), 'cleanup': {'ownedAdapterRemoved': not created_folder or not folder.exists(), 'temporaryRuntimeRemoved': not runtime.exists(), 'preexistingAdapterExisted': preexisting_adapter_existed, 'preexistingAdapterPreserved': (folder.exists() or folder.is_symlink()) if preexisting_adapter_existed else None, 'preexistingAdapterPreservationStatus': ('PRESERVED' if folder.exists() or folder.is_symlink() else 'PRESERVATION_NOT_CONFIRMED') if preexisting_adapter_existed else 'NOT_APPLICABLE', 'credentialFilesCreated': False}}

if __name__ == '__main__':
    print(json.dumps(run(json.load(sys.stdin))))
