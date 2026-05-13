# Conformance Harnesses

Four specialized test harnesses verify protocol conformance at different integration levels. Each harness is tied to a `task_type` and triggered by `pr-conformance.yml` (Task 16).

## Directory Structure

```
fabric/harnesses/
├── README.md (this file)
├── byte-equivalence/          # AC17: CBOR serialization round-trip
│   ├── Makefile              # Orchestrates build + test
│   ├── README.md             # Fixture-injection contract
│   ├── rust/                 # Rust serializer + tests
│   ├── ts/                   # TypeScript serializer
│   ├── wasm/                 # WASM bindings via wasm-pack
│   ├── fixtures/             # Stub CBOR test vectors
│   └── tests/                # Python harness tests
├── mcp-compat/                # AC19: MCP protocol compatibility
│   ├── Makefile
│   ├── README.md
│   ├── client.py             # Stub MCP client with fallback
│   ├── golden/               # Golden request/response traces
│   ├── fixtures/             # MCP-specific test data
│   └── tests/                # Protocol validation tests
├── wasm-browser/              # AC20: WASM in browser environment
│   ├── Makefile
│   ├── README.md
│   ├── playwright.config.ts  # Browser test configuration
│   ├── package.json          # Playwright + npm deps
│   ├── index.html            # Test page stub
│   ├── fixtures/             # Browser test vectors
│   └── tests/                # Playwright browser tests
├── full-mode/                 # AC21: End-to-end devnet + Arweave
│   ├── Makefile
│   ├── README.md
│   ├── devnet_roundtrip.py   # Solana devnet sign → submit → verify
│   ├── arweave_roundtrip.py  # Arweave testnet upload → fetch
│   ├── independent_verifier.py # Standalone verification binary
│   ├── fixtures/             # Mock transaction responses
│   └── tests/                # Integration test stubs
└── actions/                   # GitHub composite actions
    ├── byte-equivalence/action.yml
    ├── mcp-compat/action.yml
    ├── wasm-browser/action.yml
    └── full-mode/action.yml
```

## Harness Summary

| Harness | AC | Purpose | Entry Point | Fixture Type |
|---------|-----|---------|-------------|--------------|
| byte-equivalence | AC17 | Rust/TS/WASM serializer byte-for-byte match | `make verify` | CBOR test vectors |
| mcp-compat | AC19 | MCP client golden trace validation | `make verify` | JSON-RPC traces |
| wasm-browser | AC20 | WASM bindings in Chromium/Firefox/Safari | `make verify` | HTML + JS + Playwright |
| full-mode | AC21 | Solana devnet + Arweave testnet round-trip | `make verify` | Mock RPC responses |

## Running Harnesses

Each harness can be run standalone:

```bash
cd fabric/harnesses/<harness>
make                # alias: make test
make verify         # full conformance check
make clean          # remove artifacts
```

### Stub Mode (Default)

All harnesses run in **stub mode** by default:
- No real network dependencies
- Mock RPC responses from `fixtures/`
- Fast, deterministic, CI-friendly
- Mutating fixtures causes tests to fail (detecting regressions)

### Real Integration (opt-in)

Pass environment variables to enable real network mode:

```bash
# Byte-equivalence: compile real Rust/WASM
cd byte-equivalence && REAL_WASM=1 make verify

# MCP-compat: connect to real mnemonic-mcp server
cd mcp-compat && MCP_SERVER_URL=stdio://mnemonic-mcp make verify

# WASM-browser: test real WASM module
cd wasm-browser && REAL_WASM=1 make verify

# Full-mode: hit real devnet/testnet
cd full-mode && MNEMONIC_MODE=full \
  SOLANA_RPC_URL=https://api.devnet.solana.com \
  ARWEAVE_RPC_URL=https://arweave.net \
  make verify
```

## Fixture-Injection Contract

Real implementations inject fixtures when protocols are available:

### byte-equivalence
- Real fixtures: canonical CBOR test vectors from `mnemonic-core`
- Injection: copy to `fixtures/canonical-vectors.json`
- Validation: Rust → TS → WASM round-trip + hash comparison

### mcp-compat
- Real fixtures: recorded request/response pairs from `mnemonic-mcp`
- Injection: append to `golden/` JSONL files
- Validation: new traces match golden (byte-exact)

### wasm-browser
- Real fixtures: compiled WASM module from `mnemonic-wasm`
- Injection: copy `pkg/` to `wasm/pkg/`
- Validation: browser serialization matches Rust output

### full-mode
- Real fixtures: Solana devnet keypair + Arweave credentials
- Injection: set env vars (`SOLANA_KEYPAIR_PATH`, etc.)
- Validation: round-trip sign/verify on real chains

## Integration with PR Conformance

When a PR sets `task_type` in the description, `pr-conformance.yml` (T16) dispatches the appropriate harness:

```yaml
task_type: schema-cbor        # -> byte-equivalence harness
task_type: mcp-surface        # -> mcp-compat harness
task_type: wasm-bindings      # -> wasm-browser harness
task_type: full-mode          # -> full-mode harness
```

## Adding a New Harness

1. Create `fabric/harnesses/<name>/` with:
   - `Makefile` (targets: build, test, verify, clean)
   - `README.md` (purpose + fixture-injection contract)
   - `fixtures/` (stub test data)
   - `tests/` (test suite)

2. Create `fabric/harnesses/actions/<name>/action.yml` (GitHub composite action)

3. Document fixture-injection contract in `README.md` for integration when upstream repos available

4. Update this file with summary table

## Extending Existing Harnesses

Each harness README has an "How to Extend" section covering:
- Adding new implementations (e.g., Go serializer to byte-equivalence)
- Adding new test targets (e.g., Firefox to wasm-browser)
- Customizing schemas or protocols

## Troubleshooting

See harness-specific README files. Common patterns:

- **Stub fixtures missing**: Check `fixtures/` directory exists with test data
- **Stub fixtures mutated**: Restore from git or regenerate via `RECORD=1` (if applicable)
- **Build fails**: Check Makefile dependencies (Rust, Node.js, Python version)
- **Test timeout**: Increase harness `Makefile` timeout or `pytest` fixture timeout
- **Real integration fails**: Set environment variables for network endpoints and credentials

## References

- **AC17–AC22**: Acceptance criteria (work/coding-fabric/user-spec.md)
- **Task 17**: This task (tasks/17.md or work/coding-fabric/tech-spec.md Wave 5)
- **Task 16**: PR conformance dispatcher (pr-conformance.yml)
- **Spec §6**: QA gates by task_type (spec.md)
