import os
from pathlib import Path

import pytest


# pytest-playwright starts its session browser lazily, after earlier CLI tests
# may have exercised temporary project roots. Keep that shared test browser on
# this repository's portable runtime instead of a deleted pytest directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ["AUTODY_HOME"] = str(_PROJECT_ROOT)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
    _PROJECT_ROOT / "data" / "ms-playwright"
)
os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"


def _use_project_runtime() -> None:
    os.environ["AUTODY_HOME"] = str(_PROJECT_ROOT)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
        _PROJECT_ROOT / "data" / "ms-playwright"
    )
    os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"


@pytest.fixture(autouse=True)
def restore_project_runtime_after_each_test():
    _use_project_runtime()
    yield
    _use_project_runtime()
