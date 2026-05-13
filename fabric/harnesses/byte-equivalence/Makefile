.PHONY: all clean build test verify

all: test

build: build-rust build-wasm build-ts

build-rust:
	@echo "Building Rust serializer..."
	cd rust && cargo build --release --locked 2>&1 | head -20

build-wasm:
	@echo "Building WASM via wasm-pack..."
	cd wasm && wasm-pack build --target nodejs --dev 2>&1 | head -20

build-ts:
	@echo "Building TypeScript serializer..."
	cd ts && npm ci 2>&1 | head -10
	cd ts && npm run build 2>&1 | head -10

test: build
	@echo "Running byte-equivalence tests..."
	python3 -m pytest tests/ -v --tb=short

verify: test
	@echo "All conformance checks passed."

clean:
	rm -rf rust/target wasm/pkg ts/dist ts/node_modules
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
