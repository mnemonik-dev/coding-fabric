#!/usr/bin/env python3
"""Encrypt O2 identity/configuration for off-host backup; emit no secret bytes."""
import argparse
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path('/opt/mnemonik-o2')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipient', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('administrator access required')
    os.umask(0o077)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for name in ['secrets/id.json', 'secrets/mcp.env', 'operator.json', 'compose.json', 'Caddyfile']:
            path = ROOT / name
            if not path.is_file() or path.is_symlink():
                parser.error('required backup file missing or symlinked')
            tar.add(path, arcname=name, recursive=False)
    encrypted = subprocess.run(['age', '--encrypt', '--recipient', args.recipient],
                               input=archive.getvalue(), capture_output=True)
    if encrypted.returncode:
        parser.error('age encryption failed; check the public backup recipient')
    fd, temporary = tempfile.mkstemp(prefix='.backup-', dir=ROOT)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(encrypted.stdout)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, ROOT / 'keys-backup.tar.gz.age')
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    print('Encrypted key/configuration backup ready for off-host copy; database not included.')


if __name__ == '__main__':
    main()
