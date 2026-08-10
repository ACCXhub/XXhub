from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    browsers_path: Path


@dataclass(frozen=True)
class DoctorResult:
    home: Path
    browsers_path: Path
    executable_path: Path


def configure_runtime(home: Path) -> RuntimePaths:
    resolved_home = home.resolve()
    configured_browsers = os.environ.get("AUTODY_BROWSERS_PATH", "").strip()
    configured_program_root = os.environ.get("AUTODY_PROGRAM_ROOT", "").strip()
    packaged_browsers = (
        Path(configured_program_root).expanduser().resolve()
        / "runtime"
        / "ms-playwright"
        if configured_program_root
        else None
    )
    if configured_browsers:
        browsers_path = Path(configured_browsers).expanduser().resolve()
    elif packaged_browsers is not None and packaged_browsers.is_dir():
        browsers_path = packaged_browsers
    else:
        browsers_path = resolved_home / "data" / "ms-playwright"
    browsers_path.mkdir(parents=True, exist_ok=True)
    os.environ["AUTODY_HOME"] = str(resolved_home)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
    return RuntimePaths(home=resolved_home, browsers_path=browsers_path)


def doctor_playwright(home: Path) -> DoctorResult:
    runtime = configure_runtime(home)
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
        home=runtime.home,
        browsers_path=runtime.browsers_path,
        executable_path=executable,
    )


def repair_playwright(home: Path) -> RuntimePaths:
    runtime = configure_runtime(home)
    completed = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Chromium 安装失败，退出码：{completed.returncode}")
    return runtime
