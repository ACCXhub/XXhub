"""Capture privacy-safe documentation screenshots from the bundled fixture.

Run with: .\.venv\Scripts\python.exe scripts\capture-doc-screenshots.py
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "screenshots"
URL = "http://127.0.0.1:8766"


def wait_for_server() -> None:
    for _ in range(40):
        try:
            with urlopen(f"{URL}/api/status", timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("安全截图服务未启动")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "docs.fixture_app:app", "--host", "127.0.0.1", "--port", "8766"], cwd=ROOT)
    try:
        wait_for_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(URL); page.wait_for_timeout(300)
            page.screenshot(path=OUTPUT / "dashboard.png")
            page.get_by_role("button", name="好友管理").click(); page.screenshot(path=OUTPUT / "friends.png")
            page.get_by_role("button", name="设置").click(); page.screenshot(path=OUTPUT / "settings-modules.png", full_page=True)
            # The fixture is intentionally uninstalled by default. Install only
            # its generated first-party package and never visit Douyin.
            page.request.post(f"{URL}/api/modules/autody-test-center/install")
            page.reload(); page.get_by_role("button", name="测试中心", exact=True).click()
            page.screenshot(path=OUTPUT / "test-center.png", full_page=True)
            page.screenshot(path=OUTPUT / "test-center-preflight.png", full_page=True)
            frame = page.frame_locator("iframe")
            frame.get_by_role("button", name="移除测试中心", exact=True).click()
            page.screenshot(path=OUTPUT / "test-center-uninstall.png")
            page.request.post(f"{URL}/api/modules/autody-test-center/uninstall", data={"confirmed": True})
            page.reload(); page.get_by_role("button", name="设置").click()
            page.screenshot(path=OUTPUT / "test-center-uninstalled.png", full_page=True)
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
