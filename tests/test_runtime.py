import os
from pathlib import Path

from autody.runtime import configure_runtime, resolve_runtime_context


def test_configure_runtime_uses_project_local_playwright_directory(
    tmp_path: Path, monkeypatch
):
    for name in (
        "AUTODY_HOME",
        "AUTODY_PROGRAM_ROOT",
        "AUTODY_BROWSERS_PATH",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_SKIP_BROWSER_GC",
    ):
        monkeypatch.delenv(name, raising=False)

    runtime = configure_runtime(tmp_path)

    assert runtime.home == tmp_path.resolve()
    assert runtime.data_root == tmp_path.resolve()
    assert runtime.program_root == Path(__file__).resolve().parents[1]
    assert runtime.browsers_path == tmp_path.resolve() / "data" / "ms-playwright"
    assert os.environ["AUTODY_HOME"] == str(tmp_path.resolve())
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(runtime.browsers_path)
    assert os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] == "1"


def test_configure_runtime_overrides_appdata_playwright_path(
    tmp_path: Path, monkeypatch
):
    default_path = str(tmp_path / "external-cache" / "ms-playwright")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", default_path)

    runtime = configure_runtime(tmp_path)

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(runtime.browsers_path)
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] != default_path


def test_configure_runtime_honors_packaged_browser_path(
    tmp_path: Path, monkeypatch
):
    browsers = tmp_path / "program" / "runtime" / "ms-playwright"
    monkeypatch.setenv("AUTODY_BROWSERS_PATH", str(browsers))

    runtime = configure_runtime(tmp_path / "user-data")

    assert runtime.home == (tmp_path / "user-data").resolve()
    assert runtime.browsers_path == browsers.resolve()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers.resolve())


def test_source_runtime_does_not_treat_program_directory_as_data_or_browser_root(
    tmp_path: Path, monkeypatch
):
    program_root = tmp_path / "program"
    browsers = program_root / "runtime" / "ms-playwright"
    browsers.mkdir(parents=True)
    monkeypatch.delenv("AUTODY_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(program_root))

    runtime = configure_runtime(tmp_path / "user-data")

    assert runtime.data_root == (tmp_path / "user-data").resolve()
    assert runtime.browsers_path == (
        tmp_path / "user-data" / "data" / "ms-playwright"
    ).resolve()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(runtime.browsers_path)


def test_runtime_context_keeps_packaged_program_and_data_roots_distinct(
    tmp_path: Path, monkeypatch
):
    program_root = tmp_path / "portable"
    (program_root / "runtime").mkdir(parents=True)
    (program_root / "runtime" / "distribution-mode.txt").write_text(
        "portable", encoding="ascii"
    )
    monkeypatch.delenv("AUTODY_BROWSERS_PATH", raising=False)

    runtime = resolve_runtime_context(
        tmp_path / "user-data",
        program_root=program_root,
    )

    assert runtime.program_root == program_root.resolve()
    assert runtime.data_root == (tmp_path / "user-data").resolve()
    assert runtime.browsers_path == program_root / "runtime" / "ms-playwright"
    assert runtime.distribution_mode == "portable"
