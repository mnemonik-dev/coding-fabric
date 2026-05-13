.PHONY: all clean build test verify

all: test

build:
	@echo "Checking MCP client dependencies..."
	python3 -c "import mcp" 2>/dev/null || echo "mcp-python-sdk not available (placeholder mode)"

test: build
	@echo "Running MCP compatibility tests..."
	python3 -m pytest tests/ -v --tb=short

verify: test
	@echo "MCP compatibility checks passed."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
