.PHONY: all clean test verify devnet-test arweave-test

all: test

test:
	@echo "Running full-mode integration tests..."
	python3 -m pytest tests/ -v --tb=short

devnet-test:
	@echo "Running Solana devnet round-trip test..."
	python3 devnet_roundtrip.py

arweave-test:
	@echo "Running Arweave testnet round-trip test..."
	python3 arweave_roundtrip.py

verify-independent:
	@echo "Running independent verifier..."
	python3 independent_verifier.py

verify: test
	@echo "Full-mode conformance checks passed (stub fixtures)."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -f *.sol *.ar
