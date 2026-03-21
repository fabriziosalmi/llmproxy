"""Tests for core.plugin_engine.ast_scan and PluginSecurityError."""

import pytest
from core.plugin_engine import ast_scan, PluginSecurityError


def test_safe_code_passes():
    code = "import json\nimport re\nx = json.dumps({'a': 1})"
    assert ast_scan(code, "test_plugin") is True


def test_forbidden_os_import():
    with pytest.raises(PluginSecurityError):
        ast_scan("import os", "bad_plugin")


def test_forbidden_subprocess():
    with pytest.raises(PluginSecurityError):
        ast_scan("import subprocess", "bad_plugin")


def test_forbidden_exec_call():
    with pytest.raises(PluginSecurityError):
        ast_scan("exec('print(1)')", "bad_plugin")


def test_forbidden_eval_call():
    with pytest.raises(PluginSecurityError):
        ast_scan("eval('1+1')", "bad_plugin")


def test_forbidden_import_from_os():
    with pytest.raises(PluginSecurityError):
        ast_scan("from os import path", "bad_plugin")


def test_allowed_modules_pass():
    code = "import json\nimport re\nimport math"
    assert ast_scan(code, "safe_plugin") is True


def test_syntax_error_raises():
    with pytest.raises(PluginSecurityError):
        ast_scan("def (", "broken_plugin")
