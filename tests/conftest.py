import itertools
import uuid
from pathlib import Path
import pytest
from bookanalyst.app import create_app

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".scratch" / "tests"
_scratch_counter = itertools.count(1)


@pytest.fixture
def tmp_path(request):
    """A unique writable scratch directory.

    The default pytest ``tmp_path`` lives under a path containing ``tmp``, which
    the confined agent sandbox refuses to reopen, so tests needing scratch space
    fail with ``PermissionError``. Tests only need *a* unique directory.
    """
    directory = SCRATCH / f"{request.node.name[:40]}-{uuid.uuid4().hex[:8]}-{next(_scratch_counter)}"
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / "probe.txt"
    probe.write_text("ok", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "ok"
    return directory


@pytest.fixture
def workspace():
    return ROOT


@pytest.fixture
def app(tmp_path):
    return create_app(ROOT, tmp_path / "state")