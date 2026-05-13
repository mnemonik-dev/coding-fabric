# Byte-Equivalence Harness

## Purpose

Verifies that CBOR serialization output is bit-for-bit identical across Rust, TypeScript, and WebAssembly implementations. Detects schema drift, serialization bugs, and platform-specific issues before they cause consensus failures.

## Scaffolding Status

This harness is **SCAFFOLDING**: it provides correct test structure and integration points, but real fixtures come from protocol repos. When running in stub mode, the harness verifies that the test infrastructure works correctly without depending on external repos.

## Fixture-Injection Contract

This harness uses **stub fixtures** (small, deterministic test vectors) to verify round-trip integrity. Real fixture sets are supplied by upstream protocol repos when available.

### How to Inject Real Fixtures

When `mnemonic-core` or `mnemonic-wasm` repos are integrated:

1. Copy canonical test vectors to `fixtures/`:
   - `fixtures/canonical-vectors.json` — list of `{input: {...}, expected_cbor_hex: "..."}`
   - `fixtures/edge-cases.json` — boundary cases, nested structures, etc.

2. Update `tests/test_byte_equivalence.py`:
   - Load from real fixture files instead of inline stubs
   - Test against canonical Rust serializer output from mnemonic-core

3. Each harness run will validate:
   - Rust → TS → WASM round-trip produces identical bytes
   - Output matches canonical CBOR from fixture set
   - Any fixture mutation causes the harness to fail

### Stub Fixture Format

Stub fixtures in `fixtures/stub.cbor` are minimal valid CBOR binaries:
- Value: `{"version": 1, "nonce": 42}`
- Hex: `a2677665727369696e0118d26e6e6f6e636518…`

Mutating this file (e.g., flipping a byte) causes test failures, proving fixture sensitivity.

## How to Extend

### Add a New Serializer Implementation

1. Create `<lang>/` subdirectory (e.g., `go/`)
2. Add build target to `Makefile`
3. Implement serializer interface matching Rust signature
4. Export binary to `<lang>/dist/serializer` or equivalent
5. Update `tests/test_byte_equivalence.py` to call new binary
6. All three outputs must match byte-for-byte or test fails

### Customize CBOR Schema

1. Update Rust serializer in `rust/src/lib.rs`
2. Re-derive TypeScript types from Rust `serde` schema
3. Update WASM bindings in `wasm/src/lib.rs`
4. Re-run `make build` to validate round-trip
5. Commit schema change + updated fixture hashes

## Build Instructions

```bash
make build          # Compile all three implementations
make test           # Run byte-equivalence tests
make verify         # Full conformance check with fixture mutation detection
make clean          # Remove build artifacts
```

## Troubleshooting

- **Rust build fails**: Check `rust/Cargo.toml` dependencies (serde, ciborium, etc.)
- **WASM build fails**: Ensure `wasm-pack` is installed (`cargo install wasm-pack`)
- **TS type mismatch**: Regenerate from Rust types via `ts-rs` or manual import
- **Byte mismatch**: Enable `DEBUG=1` in test to dump hex output per serializer

## Integration with PR Conformance

When `task_type: schema-cbor` is set in PR, `pr-conformance.yml` invokes this harness via:
```bash
cd fabric/harnesses/byte-equivalence && make verify
```

Failure blocks PR merge until all three serializers produce identical bytes.
