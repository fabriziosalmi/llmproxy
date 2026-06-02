import pytest
import logging
from unittest.mock import patch
import core.wasm_runner
from core.wasm_runner import WasmRunner, _check_extism


@pytest.fixture(autouse=True)
def reset_extism_flag():
    """Reset the lazy-checked _extism_available flag before and after each test."""
    original = core.wasm_runner._extism_available
    core.wasm_runner._extism_available = None
    yield
    core.wasm_runner._extism_available = original


def test_check_extism_success_logs():
    # If extism is mockable or imported
    with patch("builtins.__import__") as mock_import:
        import sys

        # Create a mock module
        mock_extism = type(sys)("extism")
        mock_extism.__version__ = "1.0.0"
        mock_extism.extism_version = lambda: "v1.0.0-mock"
        mock_import.return_value = mock_extism

        with patch("core.wasm_runner.logger") as mock_logger:
            res = _check_extism()
            assert res is True
            mock_logger.info.assert_called_with(
                "Extism runtime detected. SDK Version: 1.0.0, C Library Version: v1.0.0-mock"
            )


def test_check_extism_import_error_logs(caplog):
    with patch(
        "builtins.__import__", side_effect=ImportError("No module named 'extism'")
    ):
        with caplog.at_level(logging.ERROR, logger="wasm_runner"):
            res = _check_extism()
            assert res is False
            assert (
                "Extism Python SDK import failed: No module named 'extism'"
                in caplog.text
            )
            assert "Ensure the 'extism' package is installed" in caplog.text


def test_check_extism_os_error_logs(caplog):
    with patch("builtins.__import__", side_effect=OSError("libextism.so not found")):
        with caplog.at_level(logging.ERROR, logger="wasm_runner"):
            res = _check_extism()
            assert res is False
            assert (
                "Extism shared C library loading failed: libextism.so not found"
                in caplog.text
            )
            assert (
                "WASM plugins require the Extism shared library ('libextism')"
                in caplog.text
            )


@pytest.mark.asyncio
async def test_wasm_runner_load_logs_error_on_failure(caplog):
    runner = WasmRunner("plugins/wasm/test.wasm")
    with patch("builtins.__import__", side_effect=ImportError("mock-fail")):
        with caplog.at_level(logging.ERROR):
            res = await runner.load()
            assert res is False
            assert (
                "WASM plugin loading aborted — Extism runtime is unavailable"
                in caplog.text
            )
