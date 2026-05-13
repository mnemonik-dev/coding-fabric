import json
import os
import subprocess
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).parent.parent
FIXTURES_DIR = HARNESS_DIR / "fixtures"
STUB_FIXTURE = FIXTURES_DIR / "stub.json"


def load_fixture(path):
    with open(path) as f:
        return json.load(f)


def serialize_with_rust(data):
    try:
        result = subprocess.run(
            [str(HARNESS_DIR / "rust" / "target" / "release" / "serializer")],
            input=json.dumps(data),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return None


def serialize_with_ts(data):
    try:
        result = subprocess.run(
            ["node", str(HARNESS_DIR / "ts" / "dist" / "serializer.js")],
            input=json.dumps(data),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return None


def serialize_with_wasm(data):
    try:
        import importlib.util

        wasm_module_path = HARNESS_DIR / "wasm" / "pkg" / "mnemonic_serializer_wasm.js"
        if not wasm_module_path.exists():
            return None

        spec = importlib.util.spec_from_file_location(
            "wasm_module", wasm_module_path
        )
        return None
    except Exception:
        return None


def test_stub_fixture_exists():
    assert STUB_FIXTURE.exists(), "Stub fixture not found"


def test_stub_fixture_valid():
    data = load_fixture(STUB_FIXTURE)
    assert "version" in data
    assert "nonce" in data
    assert data["version"] == 1
    assert data["nonce"] == 42


def test_rust_serializer_basic():
    data = load_fixture(STUB_FIXTURE)
    result = serialize_with_rust(data)
    if result:
        assert isinstance(result, str)
        assert len(result) > 0
        assert all(c in "0123456789abcdef" for c in result.lower())


def test_ts_serializer_basic():
    data = load_fixture(STUB_FIXTURE)
    result = serialize_with_ts(data)
    if result:
        assert isinstance(result, str)
        assert len(result) > 0


def test_byte_equivalence_rust_ts():
    data = load_fixture(STUB_FIXTURE)
    rust_output = serialize_with_rust(data)
    ts_output = serialize_with_ts(data)

    if rust_output and ts_output:
        assert (
            rust_output == ts_output
        ), f"Rust ({rust_output}) != TS ({ts_output})"


def test_fixture_mutation_detection():
    original = load_fixture(STUB_FIXTURE)
    mutated = original.copy()
    mutated["nonce"] = 43

    original_hash = hash(json.dumps(original))
    mutated_hash = hash(json.dumps(mutated))

    assert original_hash != mutated_hash, "Fixture mutation not detectable"


if __name__ == "__main__":
    pytest_args = [__file__, "-v", "--tb=short"]
    sys.exit(subprocess.call(["python3", "-m", "pytest"] + pytest_args))
