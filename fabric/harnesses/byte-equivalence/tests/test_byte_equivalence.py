import hashlib
import json
import os
import subprocess
import sys
import pytest
from pathlib import Path

HARNESS_DIR = Path(__file__).parent.parent
FIXTURES_DIR = HARNESS_DIR / "fixtures"
STUB_FIXTURE = FIXTURES_DIR / "stub.json"

# REQUIRE_SERIALIZERS=true converts pytest.skip into pytest.fail when the
# Rust/TS/WASM serializer binaries are not built. Set in CI so harnesses
# fail-fast when their fixtures haven't landed; default off for local dev
# where the fixtures are typically not yet compiled.
REQUIRE_SERIALIZERS = os.getenv("REQUIRE_SERIALIZERS", "false").lower() in {
    "1",
    "true",
    "yes",
}


def _missing_serializer(name: str) -> None:
    """Either skip (dev) or fail (CI) when a serializer binary is unbuilt."""
    msg = f"{name} serializer not built"
    if REQUIRE_SERIALIZERS:
        pytest.fail(
            f"{msg} (REQUIRE_SERIALIZERS=true). "
            f"Build the {name} serializer fixture before running this gate."
        )
    pytest.skip(msg)


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
    """Load and invoke WASM serializer from pkg directory."""
    try:
        wasm_module_path = HARNESS_DIR / "wasm" / "pkg" / "mnemonic_serializer_wasm.js"
        if not wasm_module_path.exists():
            return None

        # WASM would be loaded and called here; currently a stub.
        # Real implementation: load WASM module, invoke serialize(), return bytes.
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
    if result is None:
        _missing_serializer("rust")
    assert isinstance(result, str)
    assert len(result) > 0
    assert all(c in "0123456789abcdef" for c in result.lower())


def test_ts_serializer_basic():
    data = load_fixture(STUB_FIXTURE)
    result = serialize_with_ts(data)
    if result is None:
        _missing_serializer("typescript")
    assert isinstance(result, str)
    assert len(result) > 0


def test_byte_equivalence_rust_ts():
    data = load_fixture(STUB_FIXTURE)
    rust_output = serialize_with_rust(data)
    ts_output = serialize_with_ts(data)

    if rust_output is None:
        _missing_serializer("rust")
    if ts_output is None:
        _missing_serializer("typescript")

    assert (
        rust_output == ts_output
    ), f"Rust ({rust_output}) != TS ({ts_output})"


def test_fixture_mutation_detection():
    """
    Verify that a single-byte mutation in the fixture produces a different hash.
    Uses deterministic SHA256 instead of Python's non-deterministic hash().
    """
    original = load_fixture(STUB_FIXTURE)
    mutated = original.copy()
    mutated["nonce"] = 43

    original_bytes = json.dumps(original, sort_keys=True).encode("utf-8")
    mutated_bytes = json.dumps(mutated, sort_keys=True).encode("utf-8")

    original_hash = hashlib.sha256(original_bytes).hexdigest()
    mutated_hash = hashlib.sha256(mutated_bytes).hexdigest()

    assert original_hash != mutated_hash, (
        "Fixture mutation not detectable: "
        f"original hash {original_hash} == mutated hash {mutated_hash}"
    )


if __name__ == "__main__":
    pytest_args = [__file__, "-v", "--tb=short"]
    sys.exit(subprocess.call(["python3", "-m", "pytest"] + pytest_args))
