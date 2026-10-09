"""Offline public read-plan builder. No credential input, network or execution."""
import argparse, importlib.util, json, pathlib, re, sys
_spec = importlib.util.spec_from_file_location('acceptance_plan_policy', pathlib.Path(__file__).with_name('acceptance-remote.py'))
_policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_policy)
PolicyRejected = _policy.PolicyRejected
# Exact exports used by audited adapters in the pinned runtime; unknown packages fail closed.
_RUNTIME_IMPORTS = {'@jackwener/opencli/registry', '@jackwener/opencli/errors', 'node:crypto', 'node:fs/promises'}


def build_acceptance_plan(adapter_directory, namespace, commands):
    _policy.validate_acceptance_namespace(namespace)
    if not isinstance(commands, list) or not commands:
        raise PolicyRejected()
    root = pathlib.Path(adapter_directory).resolve()
    if not root.is_dir():
        raise PolicyRejected()
    files = {}

    def load(name):
        if name in files:
            return
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.(?:js|mjs)', name):
            raise PolicyRejected()
        path = root / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            raise PolicyRejected()
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            raise PolicyRejected() from None
        files[name] = text  # mark before traversal: cycles terminate, not proof of module executability
        tokens = _policy._tokens(text)
        # Builder is narrower than the executor: any dynamic import has unknown execution timing.
        for index, token in enumerate(tokens[:-1]):
            if token == ('identifier', 'import') and tokens[index + 1][1] == '(' and (not index or tokens[index - 1][1] != '.'):
                raise PolicyRejected()
        for specifier in _policy._module_specifiers(text):
            if specifier.startswith('.'):
                if not re.fullmatch(r'\./[A-Za-z0-9_-]+\.(?:js|mjs)', specifier):
                    raise PolicyRejected()
                load(specifier[2:])
            elif specifier not in _RUNTIME_IMPORTS:
                raise PolicyRejected()

    normalized = []
    for entry in commands:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise PolicyRejected()
        command, flags = entry
        if not isinstance(command, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', command) or not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
            raise PolicyRejected()
        candidates = [command + ext for ext in ('.js', '.mjs') if (root / (command + ext)).exists() or (root / (command + ext)).is_symlink()]
        if len(candidates) != 1:
            raise PolicyRejected()
        load(candidates[0])
        if _policy._metadata(files[candidates[0]])['access'] != 'read':
            raise PolicyRejected()
        normalized.append([command, list(flags)])
    _policy.validate_acceptance_namespace(namespace)
    _policy.validate_acceptance_policy({'files': files, 'commands': normalized})
    return {'site': namespace, 'files': sorted(files), 'commands': normalized}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter-directory', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--commands-json', required=True, help='Public command/flag pairs only; never credentials')
    args = parser.parse_args()
    try:
        result = build_acceptance_plan(args.adapter_directory, args.namespace, json.loads(args.commands_json))
    except (PolicyRejected, ValueError, OSError, TypeError):
        print('PUBLIC_PLAN_REJECTED', file=sys.stderr)
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
