from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class RuntimeContext:
    """The immutable roots for one AutoDy runtime invocation."""

    program_root: Path
    data_root: Path
    browsers_path: Path
    distribution_mode: str

    @property
    def home(self) -> Path:
        """Compatibility name for the data root used by doctor output."""
        return self.data_root


@dataclass(frozen=True)
class DoctorResult:
    data_root: Path
    browsers_path: Path
    executable_path: Path


def _source_program_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _distribution_mode(program_root: Path) -> str:
    marker = program_root / "runtime" / "distribution-mode.txt"
    if marker.is_file():
        mode = marker.read_text(encoding="utf-8").strip().lower()
        if mode not in {"installed", "portable"}:
            raise RuntimeError("AutoDy distribution mode marker is invalid.")
        return mode
    if (program_root / "runtime" / "python" / "python.exe").is_file():
        return "installed"
    return "source"


def resolve_runtime_context(
    data_root: Path,
    *,
    program_root: Path | None = None,
) -> RuntimeContext:
    """Resolve program, data and browser roots without conflating their roles."""
    configured_program_root = os.environ.get("AUTODY_PROGRAM_ROOT", "").strip()
    resolved_program_root = (
        program_root
        or (Path(configured_program_root).expanduser() if configured_program_root else None)
        or _source_program_root()
    ).resolve()
    resolved_data_root = data_root.expanduser().resolve()
    distribution_mode = _distribution_mode(resolved_program_root)
    configured_browsers = os.environ.get("AUTODY_BROWSERS_PATH", "").strip()
    if configured_browsers:
        browsers_path = Path(configured_browsers).expanduser().resolve()
    elif distribution_mode != "source":
        browsers_path = resolved_program_root / "runtime" / "ms-playwright"
    else:
        browsers_path = resolved_data_root / "data" / "ms-playwright"
    return RuntimeContext(
        program_root=resolved_program_root,
        data_root=resolved_data_root,
        browsers_path=browsers_path,
        distribution_mode=distribution_mode,
    )


def configure_runtime(
    data_root: Path,
    *,
    program_root: Path | None = None,
) -> RuntimeContext:
    runtime = resolve_runtime_context(data_root, program_root=program_root)
    browsers_path = runtime.browsers_path
    browsers_path.mkdir(parents=True, exist_ok=True)
    os.environ["AUTODY_HOME"] = str(runtime.data_root)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
    return runtime


def doctor_playwright(
    data_root: Path,
    *,
    program_root: Path | None = None,
) -> DoctorResult:
    runtime = configure_runtime(data_root, program_root=program_root)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path).resolve()
        if runtime.browsers_path not in executable.parents:
            raise RuntimeError(
                f"Chromium 不在项目便携目录中：{executable}"
            )
        if not executable.exists():
            raise RuntimeError(
                f"Chromium 不存在：{executable}。请运行 autody repair-playwright。"
            )
        browser = playwright.chromium.launch(headless=True)
        browser.close()
    return DoctorResult(
        data_root=runtime.data_root,
        browsers_path=runtime.browsers_path,
        executable_path=executable,
    )


def repair_playwright(
    data_root: Path,
    *,
    program_root: Path | None = None,
) -> RuntimeContext:
    runtime = configure_runtime(data_root, program_root=program_root)
    completed = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Chromium 安装失败，退出码：{completed.returncode}")
    return runtime
