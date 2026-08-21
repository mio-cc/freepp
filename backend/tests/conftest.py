"""pytest 公共 fixture。"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 让 backend/ 目录可被 import
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def tmp_auth_file(tmp_path: Path) -> Path:
    """每个测试独立的 auth.json 路径。"""
    return tmp_path / "auth.json"


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> Path:
    """每个测试独立的 config.yaml 路径。"""
    return tmp_path / "config.yaml"


@pytest.fixture(autouse=True)
def _reset_auth_failure_counter():
    """每个测试前后重置 api.auth 的失败计数与锁定状态, 保证测试隔离。"""
    try:
        import api.auth as _auth
        _auth._fail_count = 0
        _auth._locked_until = 0.0
    except Exception:
        pass
    yield
    try:
        import api.auth as _auth
        _auth._fail_count = 0
        _auth._locked_until = 0.0
    except Exception:
        pass
