"""Contract tests for the mnemonik-server role.

No live VM, no docker: assert the structural invariants that make this role
correct and distinct from the client-binary `mnemonic-mcp` role.

Run: pytest infrastructure/ansible/roles/mnemonik-server/tests/ -v
"""
from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parents[1]

# Minimal context to render the compose template for structural inspection.
_CTX = {
    "mnemonik_mcp_image": "ghcr.io/mnemonik-xyz/mnemonic-mcp",
    "mnemonik_mcp_image_tag": "latest",
    "mnemonik_mcp_container_port": 3000,
    "mnemonik_state_dir": "/mnt/vol/mnemonik",
    "mnemonik_shared_caddy_network": "vaultwarden_vaultwarden",
    "mnemonik_ollama_image": "ollama/ollama:0.21.2",
    "mnemonik_ollama_model": "qwen2.5:3b",
}


def _load(rel):
    return yaml.safe_load((ROLE / rel).read_text())


def _text(rel):
    return (ROLE / rel).read_text()


def _rendered_compose():
    from jinja2 import Environment

    raw = _text("templates/docker-compose.yml.j2")
    return yaml.safe_load(Environment().from_string(raw).render(**_CTX))


def test_disabled_by_default():
    # Master switch must default false — enabling is an explicit operator act.
    assert _load("defaults/main.yml")["mnemonik_server_enabled"] is False


def test_pulls_image_never_builds_rust():
    # The box pulls the GHCR image; no service builds from a Dockerfile.
    assert "ghcr.io/mnemonik-xyz/mnemonic-mcp" in _load("defaults/main.yml")["mnemonik_mcp_image"]
    for name, svc in _rendered_compose()["services"].items():
        assert "build" not in svc, f"{name} must not build locally"


def test_drops_nginx_and_certbot():
    # Caddy fronts TLS; only mcp + ollama remain, and no host 80/443 bind.
    compose = _rendered_compose()
    assert set(compose["services"]) == {"mcp", "ollama"}
    for svc in compose["services"].values():
        assert not svc.get("ports"), "no host port publish — Caddy fronts it"


def test_joins_shared_caddy_network_with_alias():
    compose = _rendered_compose()
    mcp_nets = compose["services"]["mcp"]["networks"]
    assert "vaultwarden_vaultwarden" in mcp_nets
    assert "mnemonik-mcp" in mcp_nets["vaultwarden_vaultwarden"]["aliases"]
    snippet = _text("templates/Caddyfile.snippet.j2")
    assert "reverse_proxy http://mnemonik-mcp:" in snippet


def test_state_on_persistent_volume():
    # Identity (/keypair) + attestation DB (/data) bind-mounted off the volume.
    volumes = _rendered_compose()["services"]["mcp"]["volumes"]
    assert "/mnt/vol/mnemonik/keypair:/keypair" in volumes
    assert "/mnt/vol/mnemonik/data:/data" in volumes
    assert "/dev/sdb" in _text("tasks/main.yml")  # discovers the volume mount


def test_secrets_asserted_and_have_no_default():
    # Fails loudly if enabled without the hosted-mode secrets.
    defaults = _load("defaults/main.yml")
    assert "mnemonik_mcp_jwt_secret" not in defaults
    assert "mnemonik_mcp_refresh_salt" not in defaults
    tasks = _text("tasks/main.yml")
    assert "Assert required MCP secrets are present" in tasks


def test_ollama_model_pulled_not_built():
    # Stock ollama image + a post-up pull replaces the monorepo custom image.
    compose = _text("templates/docker-compose.yml.j2")
    assert "ollama/ollama" in _load("defaults/main.yml")["mnemonik_ollama_image"]
    tasks = _text("tasks/main.yml")
    assert "ollama pull" in tasks


def test_distinct_from_client_binary_role():
    # Guard against confusion with the `mnemonic-mcp` client-binary role:
    # this role must not install an npm package or a PATH binary.
    tasks = _text("tasks/main.yml")
    assert "npm" not in tasks
    assert "/usr/local/bin" not in tasks
