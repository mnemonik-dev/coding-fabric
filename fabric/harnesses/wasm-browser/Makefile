.PHONY: all clean build install test verify

all: test

install:
	@echo "Installing Playwright dependencies..."
	npm install --silent 2>&1 | head -10

build: install
	@echo "Building WASM bindings..."
	@if [ -d "wasm" ]; then cd wasm && npm run build 2>&1 | head -10; fi

test: build
	@echo "Running WASM browser tests..."
	npx playwright test --config=playwright.config.ts 2>&1

verify: test
	@echo "WASM browser conformance checks passed."

clean:
	rm -rf node_modules dist .playwright
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
