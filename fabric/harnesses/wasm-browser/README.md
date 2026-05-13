# WASM-Browser Harness

## Purpose

Verifies that WASM-compiled serializer bindings work correctly in a headless browser environment and produce byte-equivalence with the Rust reference implementation. Detects platform-specific issues, binding errors, and WASM initialization failures before production use.

## Fixture-Injection Contract

This harness loads WASM bindings via Playwright and runs serialization tests in a real browser context (Chromium).

### How to Inject Real Fixtures

When `mnemonic-wasm` is available:

1. Copy WASM bindings and fixtures:
   - `wasm/pkg/` — compiled WASM module from `wasm-pack build`
   - `fixtures/wasm-test-vectors.json` — test vectors for browser execution

2. Update `tests/wasm-equivalence.spec.ts`:
   - Replace stub WASM import with real path to compiled module
   - Load real test vectors from fixtures
   - Validate output against Rust reference hashes

3. Each harness run validates:
   - WASM module loads and initializes in browser
   - Serializer functions callable from browser JavaScript
   - Output matches Rust reference byte-for-byte
   - Performance within expected bounds (< 100ms per serialization)

### Stub Fixture Format

Stub fixtures are minimal test HTML + JavaScript that exercises the WASM API:
```html
<script type="module">
  import init, { serialize_cbor } from './wasm/pkg/mnemonic_serializer_wasm.js';
  await init();
  const result = serialize_cbor(1, 42);
  console.log('WASM result: ' + result);
</script>
```

Mutating the expected output or WASM path causes test failure.

## How to Extend

### Add a New Browser Target

1. Update `playwright.config.ts` to test additional browsers (Firefox, WebKit, etc.)
2. Run tests with `npx playwright test --project=firefox`
3. Verify byte-equivalence across all targets
4. Update CI matrix in `pr-conformance.yml` if needed

### Add Performance Benchmarks

1. Create `tests/wasm-performance.spec.ts`
2. Measure serialization latency per browser
3. Assert < 100ms per 1000 operations (configurable)
4. Generate performance report as test artifact

### Customize WASM API

1. Update Rust `wasm/src/lib.rs` with new `#[wasm_bindgen]` functions
2. Rebuild WASM via `wasm-pack build`
3. Update TypeScript bindings in test imports
4. Run `make test` to validate in browser

## Build Instructions

```bash
make install        # Install npm dependencies
make build          # Compile WASM bindings
make test           # Run browser tests via Playwright
make verify         # Full conformance check
make clean          # Remove artifacts
```

## Troubleshooting

- **WASM module not found**: Check `wasm-pack build` output; ensure `pkg/` directory exists
- **Browser test timeout**: Increase Playwright timeout in `playwright.config.ts`
- **Byte mismatch with Rust**: Enable verbose logging to see WASM output; compare hex dumps
- **Chromium not found**: Run `npx playwright install chromium`

## Integration with PR Conformance

When `task_type: wasm-bindings` is set in PR, `pr-conformance.yml` invokes:
```bash
cd fabric/harnesses/wasm-browser && make verify
```

Failure blocks PR merge until WASM bindings work in all configured browser targets.
