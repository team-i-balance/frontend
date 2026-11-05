"""Verify the built EXE using a real console window containing only synthetic CLI rows."""

import ctypes
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TITLE = "KP Guard fixture - Codex"
DRAFT = "sample 010-2345-6789"


def console_fixture():
    # Explicit console output also works when the runner redirects stdio.
    ctypes.windll.kernel32.SetConsoleTitleW(TITLE)
    with open("CONOUT$", "w", encoding="utf-8") as output:
        print("OpenAI Codex (fixture)\nprior reply containing HISTORY_ONLY\n", file=output, flush=True)
        text = sys.stdin.readline().strip()
        print(f"› {text}\n\n  ? for shortcuts\n", file=output, flush=True)
        sys.stdin.readline()


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
    from smoke_browser import FixtureBackend
    from agent.overlay import windows as win

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
        console = subprocess.Popen(
            [sys.executable, __file__, "--fixture"],
            stdin=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        exits.callback(terminate, console)
        window = win.u.WindowControl(searchDepth=1, RegexName=r".*KP Guard fixture - Codex.*")
        wait_for(lambda: window.Exists(0.3, 0.05), "console window missing")
        window.SetActive(waitTime=0)
        console.stdin.write(DRAFT + "\n")
        console.stdin.flush()
        wait_for(lambda: DRAFT in previews, "packaged GUI did not scan the visible CLI composer")
        assert all("HISTORY_ONLY" not in text for text in previews), "terminal history reached the server"
        find = win.api(win.user, "FindWindowExW", W.HWND, W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR)
        visible = win.api(win.user, "IsWindowVisible", W.BOOL, W.HWND)

        def painted():
            hwnd = find(None, None, "KPGuardTextMark", None)
            while hwnd:
                pid = W.DWORD()
                win.pid_for(hwnd, ctypes.byref(pid))
                if win.executable(pid.value) == path.name.lower() and visible(hwnd):
                    return True
                hwnd = find(None, hwnd, "KPGuardTextMark", None)
            return False

        # A first-run tray notification may have appeared while the executable loaded.
        window.SetActive(waitTime=0)
        try:
            wait_for(painted, "no visible native word mark from packaged executable")
        except AssertionError:
            active, pid = win.foreground(), W.DWORD()
            win.pid_for(active, ctypes.byref(pid))
            print(
                f"active={win.executable(pid.value)}, fixture={active == window.NativeWindowHandle}, received={len(previews)}",
                flush=True,
            )
            with win.Reader() as reader:
                source = reader.sample()
                if isinstance(source, win.Snapshot) and source.draft.text == DRAFT:
                    a = DRAFT.index("010")
                    print(f"source bounds={list(source.rectangles(a, len(DRAFT)))}", flush=True)
            raise
        assert app.poll() is None
        print(
            "Packaged executable: Windows console composer -> fixture server -> native visible mark passed."
        )


if __name__ == "__main__":
    if sys.argv[1] == "--fixture":
        console_fixture()
    else:
        check(Path(sys.argv[1]))
