#!/usr/bin/env python3
"""
Unit tests for validate_matrix.py
Asserts return type consistency and exit behavior.
"""

import sys
import os
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "files"))

import validate_matrix as vm


def test_validate_matrix_return_type_on_success():
    """Assert validate_matrix returns (int, str) tuple on success."""
    # Temporarily override CONFIG_DIR to a dir with valid matrix
    original_dir = vm.CONFIG_DIR
    test_dir = Path.home() / ".fabric" / "ruflo" / "topics"
    vm.CONFIG_DIR = test_dir

    if test_dir.exists():
        result = vm.validate_matrix()
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 2, f"Expected 2-tuple, got {len(result)}-tuple"
        exit_code, message = result
        assert isinstance(exit_code, int), f"exit_code should be int, got {type(exit_code)}"
        assert isinstance(message, str), f"message should be str, got {type(message)}"
        assert exit_code == 0, f"Expected exit_code=0 on success, got {exit_code}"
        assert "OK" in message, f"Expected 'OK' in message, got: {message}"

    vm.CONFIG_DIR = original_dir


def test_validate_matrix_return_type_on_error():
    """Assert validate_matrix returns (int, str) tuple on error."""
    original_dir = vm.CONFIG_DIR
    # Set to nonexistent dir to trigger error
    vm.CONFIG_DIR = Path("/nonexistent/path/ruflo/topics")

    result = vm.validate_matrix()
    assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
    assert len(result) == 2, f"Expected 2-tuple, got {len(result)}-tuple"
    exit_code, message = result
    assert isinstance(exit_code, int), f"exit_code should be int, got {type(exit_code)}"
    assert isinstance(message, str), f"message should be str, got {type(message)}"
    assert exit_code != 0, f"Expected non-zero exit_code on error, got {exit_code}"
    assert "Error" in message or "does not exist" in message, f"Expected error message, got: {message}"

    vm.CONFIG_DIR = original_dir


if __name__ == "__main__":
    print("Running test_validate_matrix_return_type_on_success...")
    try:
        test_validate_matrix_return_type_on_success()
        print("PASS: return type consistent on success")
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    print("Running test_validate_matrix_return_type_on_error...")
    try:
        test_validate_matrix_return_type_on_error()
        print("PASS: return type consistent on error")
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    print("\nAll tests passed!")
    sys.exit(0)
