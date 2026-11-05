"""Verify the built EXE in a real Chrome window showing a claude.ai composer (no extension).

Scope (2026-11-05): Claude in the browser only. The page and the team server are local fixtures:
the page is served by Playwright for https://claude.ai/ and never reaches Claude.

python tools/smoke_windows_package.py dist/KPGuard/KPGuard.exe   (interactive Windows desktop)
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
sys.path.insert(0, str(TOOLS))
DRAFT = "sample 010-2345-6789"
PAGE = (
    "<title>KP Native Fixture</title><body><button>outside</button>"
    "<style>[contenteditable]{display:block;margin:50px;width:480px;min-height:75px;font:20px sans-serif}</style>"
    '<div role="textbox" aria-multiline="true" aria-label="Reply to Claude" contenteditable="true"'
    ' class="tiptap ProseMirror"></div>'
)


def wait_for(predicate, message, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def terminate(process):
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


def check(path):
    from ctypes import wintypes as W

    from playwright.sync_api import sync_playwright
    from smoke_browser import FixtureBackend
    from smoke_native import activate_fixture, type_direct

    from agent.main import _dpi_aware
    from agent.overlay import windows as win

    _dpi_aware()  # 화면 좌표(마우스·표시 창)를 실제 픽셀로 맞춘다

    class Backend(FixtureBackend):
        def handle(self, route, body):
            if route == "/process/preview":
                previews.append(body.get("text"))
            return super().handle(route, body)

    previews = []
    with ExitStack() as exits:
        backend = exits.enter_context(Backend())
        local = Path(exits.enter_context(TemporaryDirectory()))
        settings = local / "KPGuard"
        settings.mkdir()
        (settings / "settings.json").write_text(
            json.dumps(
                {
                    "autostart_asked": True,
                    "clipboard_watch": False,
                    "model_enabled": False,
                    "server_url": backend.url,
                }
            ),
            encoding="utf-8",
        )
        app = subprocess.Popen(
            [str(path.resolve()), "--background"],
            env={**os.environ, "LOCALAPPDATA": str(local), "KPGUARD_SERVER_URL": backend.url},
        )
        exits.callback(terminate, app)
        wait_for(
            lambda: "/health" in backend.requests, "packaged GUI failed to reach the fixture backend", 45
        )

        play = exits.enter_context(sync_playwright())
        browser = play.chromium.launch(channel="chrome", headless=False)
        exits.callback(browser.close)
        ctx = browser.new_context(no_viewport=True)

        def fixture(route):
            if urlsplit(route.request.url).hostname == "claude.ai" and route.request.resource_type == "document":
                route.fulfill(content_type="text/html", body=PAGE)
            else:
                route.abort()

        ctx.route("**/*", fixture)
        page = ctx.new_page()
        page.goto("https://claude.ai/new")
        page.bring_to_front()
        field = page.locator("[contenteditable]")
        field.click()
        hwnd = activate_fixture(page, field)
        type_direct(page, hwnd, DRAFT)
        wait_for(lambda: DRAFT in previews, "packaged GUI did not scan the claude.ai composer")

        find = win.api(win.user, "FindWindowExW", W.HWND, W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR)
        visible = win.api(win.user, "IsWindowVisible", W.BOOL, W.HWND)

        def painted():
            mark = find(None, None, "KPGuardTextMark", None)
            while mark:
                pid = W.DWORD()
                win.pid_for(mark, ctypes.byref(pid))
                if win.executable(pid.value) == path.name.lower() and visible(mark):
                    return True
                mark = find(None, mark, "KPGuardTextMark", None)
            return False

        # A first-run tray notification may have appeared while the executable loaded.
        win.u.WindowControl(searchDepth=1, RegexName=".*KP Native Fixture.*").SetActive(waitTime=0)
        try:
            wait_for(painted, "no visible native word mark from packaged executable")
        except AssertionError:
            active, pid = win.foreground(), W.DWORD()
            win.pid_for(active, ctypes.byref(pid))
            print(
                f"active={win.executable(pid.value)}, fixture={active == hwnd}, received={len(previews)}",
                flush=True,
            )
            raise
        assert app.poll() is None
        print("Packaged executable: Chrome claude.ai composer -> fixture server -> native visible mark passed.")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
