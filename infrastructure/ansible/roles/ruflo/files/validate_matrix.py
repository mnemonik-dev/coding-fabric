#!/usr/bin/env python3
"""
Validates ruflo per-topic config matrix against canonical spec (tech-spec §2.4).

Assertion: All 8 on-disk config files match the expected matrix exactly.
Exit code 0 on success; non-zero on mismatch.
"""

import os
import sys
import yaml
import json
from pathlib import Path

# Canonical matrix from tech-spec §2.4 (must match defaults/main.yml)
CANONICAL_MATRIX = [
    {
        "topic": "core",
        "engine": "claude-code",
        "autopilot": "off",
        "aidefence": "off",
        "rag_memory": "on",
        "mnemonic_mode": "local",
        "memory_namespace": "mnemonic-core",
    },
    {
        "topic": "mcp",
        "engine": "claude-code",
        "autopilot": "off",
        "aidefence": "off",
        "rag_memory": "on",
        "mnemonic_mode": "local",
        "memory_namespace": "mnemonic-mcp",
    },
    {
        "topic": "wasm",
        "engine": "claude-code",
        "autopilot": "off",
        "aidefence": "on",
        "rag_memory": "on",
        "mnemonic_mode": "local",
        "memory_namespace": "mnemonic-wasm",
    },
    {
        "topic": "demo-client",
        "engine": "codex",
        "autopilot": "on",
        "aidefence": "on",
        "rag_memory": "on",
        "mnemonic_mode": "local",
        "memory_namespace": "mnemonic-demo-client",
    },
    {
        "topic": "docs",
        "engine": "claude-code",
        "autopilot": "on",
        "aidefence": "on",
        "rag_memory": "on",
        "mnemonic_mode": "local",
        "memory_namespace": "mnemonic-docs",
    },
    {
        "topic": "loop",
        "engine": "claude-code",
        "autopilot": "off",
        "aidefence": "on",
        "rag_memory": "on",
        "mnemonic_mode": "local",
        "memory_namespace": "mnemonic-loop",
    },
    {
        "topic": "protocol-qa",
        "engine": "claude-code",
        "autopilot": "off",
        "aidefence": "on",
        "rag_memory": "off",
        "mnemonic_mode": "full",
        "memory_namespace": "mnemonic-qa",
    },
    {
        "topic": "ops",
        "engine": "claude-code",
        "autopilot": "off",
        "aidefence": "on",
        "rag_memory": "on",
        "mnemonic_mode": "local",
        "memory_namespace": "mnemonic-ops",
    },
]

CONFIG_DIR = Path.home() / ".fabric" / "ruflo" / "topics"


def validate_matrix():
    """Validate all 8 per-topic config files match canonical matrix."""
    errors = []

    if not CONFIG_DIR.exists():
        return 1, f"Error: config directory {CONFIG_DIR} does not exist"

    for expected_config in CANONICAL_MATRIX:
        topic = expected_config["topic"]
        config_file = CONFIG_DIR / f"{topic}.yml"

        # Check file exists
        if not config_file.exists():
            errors.append(f"Missing: {topic}.yml")
            continue

        # Load and parse YAML
        try:
            with open(config_file, "r") as f:
                on_disk = yaml.safe_load(f)
        except Exception as e:
            errors.append(f"Parse error in {topic}.yml: {e}")
            continue

        # Validate each key matches canonical
        for key, expected_value in expected_config.items():
            if key not in on_disk:
                errors.append(f"{topic}.yml: missing key '{key}'")
            elif on_disk[key] != expected_value:
                errors.append(
                    f"{topic}.yml: {key} = {on_disk[key]} "
                    f"(expected {expected_value})"
                )

    if errors:
        print("Validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    print(f"Validation OK: {len(CANONICAL_MATRIX)} topics")
    return 0


if __name__ == "__main__":
    exit_code, message = validate_matrix()
    if exit_code != 0:
        print(message, file=sys.stderr)
    sys.exit(exit_code)
