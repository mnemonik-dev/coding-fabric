#!/usr/bin/env python3
"""Administrator-only first installation on a dedicated O2 staging VM.

Requires Docker Compose v2 and Ubuntu's python3-cryptography package.
Does not change users, sudoers, firewall rules, DNS, or existing installations.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

ROOT = Path('/opt/mnemonik-o2')
# Monorepo e8b6e2b (sha-e8b6e2b): adds the anonymous mnemonic_operator_proof tool.
IMAGE = ('ghcr.io/mnemonik-xyz/mnemonic-mcp@sha256:'
         'cd5898b31005809a5ca595130c2448bacdd37e86b83d31fc3f6558f22e217acb')


def hostname(value):
    labels = value.split('.')
    if (len(value) > 253 or len(labels) < 2 or
            not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels) or
            not re.fullmatch(r'[a-z]{2,63}', labels[-1])):
        raise ValueError('Use a lowercase DNS hostname, for example mcp-o2-staging.example.com')
    return value


def public_key(raw):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    n, result = int.from_bytes(raw, 'big'), ''
    while n:
        n, digit = divmod(n, 58)
        result = alphabet[digit] + result
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + result


def compose():
    return {
        'name': 'mnemonik-o2',
        'services': {
            'mcp': {
                'image': IMAGE, 'platform': 'linux/amd64', 'user': '10001:10001',
                'restart': 'unless-stopped', 'env_file': ['./secrets/mcp.env'],
                'security_opt': ['no-new-privileges:true'], 'cap_drop': ['ALL'],
                'volumes': [
                    {'type': 'bind', 'source': str(ROOT / 'data'), 'target': '/data',
                     'bind': {'create_host_path': False}},
                    {'type': 'bind', 'source': str(ROOT / 'secrets/id.json'),
                     'target': '/keypair/id.json', 'read_only': True,
                     'bind': {'create_host_path': False}},
                ],
                'ports': ['127.0.0.1:3000:3000'],
                'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}},
            },
            'caddy': {
                'image': 'caddy@sha256:748016f285ed8c43a9ce6e3aed6d92d3009d90ca41157950880f40beaf3ff62b',
                'restart': 'unless-stopped',
                'ports': ['80:80', '443:443'],
                'volumes': ['./Caddyfile:/etc/caddy/Caddyfile:ro',
                            './caddy-data:/data', './caddy-config:/config'],
            },
        },
    }


def write(path, value, uid=0, gid=0):
    with path.open('x') as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())
    path.chmod(0o600)
    os.chown(path, uid, gid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hostname', required=True, type=hostname)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('run using the existing administrator account and sudo')
    if os.uname().machine != 'x86_64':
        parser.error('this published image requires a Linux AMD64 VM')
    if ROOT.exists() or ROOT.is_symlink():
        parser.error('/opt/mnemonik-o2 already exists; refusing to overwrite identity or configuration')
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

    subprocess.run(['docker', 'compose', 'version'], check=True)
    subprocess.run(['docker', 'info', '--format', '{{.OSType}}'], check=True)
    os.umask(0o077)
    ROOT.mkdir(mode=0o700)
    for directory in ['secrets', 'data', 'caddy-data', 'caddy-config']:
        (ROOT / directory).mkdir(mode=0o700)
    os.chown(ROOT / 'data', 10001, 10001)
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    write(ROOT / 'secrets/id.json', json.dumps(list(seed + public)) + '\n', 10001, 10001)
    env = {
        'MCP_PUBLIC_BASE_URL': 'https://' + args.hostname,
        'MCP_JWT_SECRET': base64.b64encode(secrets.token_bytes(32)).decode(),
        'MCP_REFRESH_SALT': base64.b64encode(secrets.token_bytes(32)).decode(),
        'MNEMONIC_KEYPAIR_PATH': '/keypair/id.json',
        'DATABASE_PATH': '/data/attestations.db',
        'RAG_CHUNK_DIR': '/data/rag_chunks', 'FASTEMBED_CACHE_DIR': '/data/model-cache',
        'STORAGE_MODE': 'full', 'EMBED_PROVIDER': 'fastembed', 'PAYMENT_MODE': 'none',
        'ANCHORING_NETWORK': 'devnet', 'SOLANA_RPC_URL': 'https://api.devnet.solana.com',
        'IRYS_GATEWAY_URL': 'https://devnet.irys.xyz', 'RUST_LOG': 'info',
    }
    write(ROOT / 'secrets/mcp.env', ''.join(f'{key}={value}\n' for key, value in env.items()))
    write(ROOT / 'compose.json', json.dumps(compose(), indent=2) + '\n')
    write(ROOT / 'Caddyfile', args.hostname + ' {\n  reverse_proxy mcp:3000\n}\n')
    write(ROOT / 'operator.json', json.dumps({
        'id': 'o2', 'baseUrl': 'https://' + args.hostname, 'publicKey': public_key(public),
    }, indent=2) + '\n')
    print('Created O2 configuration. No services started and no funds spent.')
    print('Public operator key:', public_key(public))
    print('Start: sudo docker compose -f /opt/mnemonik-o2/compose.json up -d --wait --wait-timeout 300')


if __name__ == '__main__':
    main()
