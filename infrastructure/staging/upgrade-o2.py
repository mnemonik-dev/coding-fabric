#!/usr/bin/env python3
"""Administrator-only MCP upgrade for an existing O2 installation.

Sets the MCP service image in /opt/mnemonik-o2/compose.json to the reviewed
digest in bootstrap-o2.py, and the storage settings in secrets/mcp.env to
bootstrap-o2.py's STORAGE_ENV. Superseded gateway variables are removed; every
other line (identity path, secrets, data paths) stays as it is. Each changed
file keeps its previous version (*.prev) for rollback. Prints key names only.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re

ROOT = Path('/opt/mnemonik-o2')
IMAGE_RE = re.compile(r'^ghcr\.io/mnemonik-xyz/mnemonic-mcp@sha256:[0-9a-f]{64}$')


NO_CHANGES = 'NO_CHANGES'


def _bootstrap():
    spec = importlib.util.spec_from_file_location('bootstrap_o2', Path(__file__).with_name('bootstrap-o2.py'))
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    return bootstrap


def reviewed_image():
    return _bootstrap().IMAGE


def reviewed_storage_env():
    return dict(_bootstrap().STORAGE_ENV)


def _superseded(key, desired):
    """Older gateway variables replaced by ARWEAVE_GATEWAY_URL."""
    return key not in desired and (key == 'ARWEAVE_URL' or key.endswith('_GATEWAY_URL'))


def reconcile_env(root, desired):
    """Return the sorted key names changed in secrets/mcp.env (empty: no write)."""
    env_path = root / 'secrets/mcp.env'
    if env_path.is_symlink() or not env_path.is_file():
        raise SystemExit('existing O2 secrets/mcp.env is missing; run prepare first')
    original = env_path.read_text()
    lines, seen, changed = [], set(), set()
    for line in original.splitlines():
        key, sep, value = line.partition('=')
        key = key.strip()
        if not sep or key.startswith('#'):
            lines.append(line)
        elif _superseded(key, desired):
            changed.add(key)
        elif key in desired:
            seen.add(key)
            if value != desired[key]:
                changed.add(key)
            lines.append(f'{key}={desired[key]}')
        else:
            lines.append(line)
    for key, value in desired.items():
        if key not in seen:
            changed.add(key)
            lines.append(f'{key}={value}')
    if changed:
        write(root / 'secrets/mcp.env.prev', original)
        write(env_path, '\n'.join(lines) + '\n')
    return sorted(changed)


def write(path, text):
    """Atomic durable write: tempfile, fsync, os.replace, fsync parent directory."""
    temporary = path.with_name(path.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def upgrade(root, image):
    """Return (old, new) MCP image; write nothing when they already match."""
    if not IMAGE_RE.match(image):
        raise SystemExit('reviewed image must be a pinned mnemonic-mcp digest')
    compose_path = root / 'compose.json'
    if compose_path.is_symlink() or not compose_path.is_file():
        raise SystemExit('existing O2 compose.json is missing; run prepare first')
    original = compose_path.read_text()
    data = json.loads(original)
    old = data['services']['mcp']['image']
    if not IMAGE_RE.match(old):
        raise SystemExit('installed MCP image is not a pinned mnemonic-mcp digest; refusing to edit')
    if old == image:
        return old, image
    write(root / 'compose.json.prev', original)
    data['services']['mcp']['image'] = image
    write(compose_path, json.dumps(data, indent=2) + '\n')
    return old, image


def clear_previous(root):
    """Drop rollback copies from an earlier run so rollback restores only this one."""
    for name in ('compose.json.prev', 'secrets/mcp.env.prev'):
        path = root / name
        if path.is_file() and not path.is_symlink():
            path.unlink()


def rollback(root):
    restored = []
    for name, target in (('compose.json.prev', 'compose.json'),
                         ('secrets/mcp.env.prev', 'secrets/mcp.env')):
        previous = root / name
        if previous.is_file() and not previous.is_symlink():
            write(root / target, previous.read_text())
            restored.append(target)
    if not restored:
        raise SystemExit('no previous configuration to restore')
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true', help='restore the *.prev files')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('run using the existing administrator account and sudo')
    if args.rollback:
        print('Restored previous: ' + ', '.join(rollback(ROOT)))
        return
    clear_previous(ROOT)
    old, new = upgrade(ROOT, reviewed_image())
    keys = reconcile_env(ROOT, reviewed_storage_env())
    if old != new:
        print(f'MCP image: {old} -> {new}')
    if keys:
        print('MCP storage settings updated: ' + ', '.join(keys))
    if old == new and not keys:
        print(NO_CHANGES)


if __name__ == '__main__':
    main()
