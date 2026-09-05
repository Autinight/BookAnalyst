from pathlib import Path
import pytest
from bookanalyst.app import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace():
    return ROOT


@pytest.fixture
def app(tmp_path):
    return create_app(ROOT, tmp_path / "state")
