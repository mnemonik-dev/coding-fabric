# Full-Mode Harness

## Purpose

End-to-end integration testing: signs attestations on Solana devnet, uploads to Arweave testnet, and verifies recovery via an independent verifier. Detects network failures, serialization issues, and cryptographic inconsistencies before production attestations.


## Scaffolding Status

This harness is **SCAFFOLDING**: it provides correct test structure and integration points, but real fixtures come from protocol repos. When running in stub mode, the harness verifies that the test infrastructure works correctly without depending on external repos.

## Fixture-Injection Contract

This harness mocks devnet and testnet RPC to avoid real network dependency during CI runs. Real integration happens in PR when `task_type: full-mode` is set.

### How to Inject Real Fixtures

When running against live devnet/testnet:

1. Configure real RPC endpoints:
   - `SOLANA_RPC_URL=https://api.devnet.solana.com` (or local validator)
   - `ARWEAVE_RPC_URL=https://arweave.net` (or testnet instance)

2. Set Solana keypair:
   - `SOLANA_KEYPAIR_PATH=/path/to/devnet/keypair.json` (fund devnet SOL via faucet)
   - Ensure keypair file is not committed

3. Run devnet/arweave tests:
   ```bash
   make devnet-test          # Live Solana devnet round-trip
   make arweave-test         # Live Arweave testnet upload/fetch
   make verify-independent   # Run independent verifier against real attestation
   ```

4. Each real test validates:
   - Attestation signs correctly on devnet
   - Transaction confirms within timeout
   - Arweave upload succeeds and is retrievable
   - Independent verifier recovers full attestation from on-chain hashes

### Stub Fixture Format

Stub fixtures in `fixtures/` are minimal valid attestations:
- `fixtures/stub-attestation.json` — mock signed attestation object
- `fixtures/stub-solana-tx.json` — mock Solana transaction receipt
- `fixtures/stub-arweave-tx.json` — mock Arweave transaction response

Mutating transaction hashes or signatures causes recovery to fail.

## How to Extend

### Add a New Blockchain Target

1. Create `<chain>_roundtrip.py` (e.g., `arbitrum_roundtrip.py`)
2. Implement sign/submit/verify flow matching Solana pattern
3. Call independent verifier to reconstruct attestation
4. Add to `Makefile` targets
5. Update CI matrix in `pr-conformance.yml`

### Customize Attestation Schema

1. Update `independent_verifier.py` to match new schema
2. Regenerate stub fixtures with new format
3. Run all roundtrip tests
4. Verify hashes and signatures match canonical values

### Fine-Tune Recovery Timeouts

1. Edit timeout constants in `devnet_roundtrip.py`
2. Run devnet test with increased verbosity: `DEBUG=1 make devnet-test`
3. Adjust based on observed confirmation latency

## Build Instructions

```bash
make test                    # Run stub tests (no network)
make devnet-test             # Live Solana devnet (requires SOL + keypair)
make arweave-test            # Live Arweave testnet
make verify-independent      # Run independent verifier on stub
make verify                  # Full conformance check with stubs
make clean                   # Remove artifacts
```

## Troubleshooting

- **Devnet timeout**: Increase `CONFIRM_TIMEOUT` or check network connectivity
- **Arweave upload fails**: Verify testnet endpoint; check account balance
- **Independent verifier fails**: Ensure CBOR schema matches signer
- **Keypair not found**: Check `SOLANA_KEYPAIR_PATH`; fund account on devnet faucet

## Integration with PR Conformance

When `task_type: full-mode` is set in PR, `pr-conformance.yml` invokes:
```bash
cd fabric/harnesses/full-mode && MNEMONIC_MODE=full make verify
```

With MNEMONIC_MODE=full, real devnet/testnet integration is enabled. Failure blocks PR merge.
