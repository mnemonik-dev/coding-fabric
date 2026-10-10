#!/usr/bin/env python3
"""Administrator-only MCP image upgrade for an existing O2 installation.

Changes only the MCP service image in /opt/mnemonik-o2/compose.json to the
reviewed digest in bootstrap-o2.py. Identity, secrets, data and Caddy stay as
they are. The previous file is kept as compose.json.prev for rollback.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re

ROOT = Path('/opt/mnemonik-o2')
IMAGE_RE = re.compile(r'^ghcr\.io/mnemonik-xyz/mnemonic-mcp@sha256:[0-9a-f]{64}$')


def reviewed_image():
    spec = importlib.util.spec_from_file_location('bootstrap_o2', Path(__file__).with_name('bootstrap-o2.py'))
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    return bootstrap.IMAGE


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


def rollback(root):
    previous = root / 'compose.json.prev'
    if previous.is_symlink() or not previous.is_file():
        raise SystemExit('no compose.json.prev to restore')
    write(root / 'compose.json', previous.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true', help='restore compose.json.prev')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('run using the existing administrator account and sudo')
    if args.rollback:
        rollback(ROOT)
        print('Restored the previous MCP image configuration.')
        return
    old, new = upgrade(ROOT, reviewed_image())
    print('MCP image unchanged.' if old == new else f'MCP image: {old} -> {new}')


if __name__ == '__main__':
    main()
