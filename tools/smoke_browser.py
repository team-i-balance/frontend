"""Browser extension regression: both editor types, typing, click-away and editor replacement.

Run with Playwright Chromium installed. --fixture-runtime also supports a headless-shell binary.
The fixture never contacts ChatGPT, Claude or a production team server.
"""

import argparse
import secrets
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import config
from agent.browser.actions import BrowserActions
from agent.browser.bridge import BrowserBridge
from agent.browser.peer import Peer
from agent.server.client import ServerClient
from tests.fake_backend import FakeBackend, PHONE

EXT = ROOT / "browser-extension"
PHONE_TEXT = "010-2345-6789"


class FixtureBackend(FakeBackend):
    def handle(self, path, body):
        if path == "/process/preview":
            return 200, {
                "spans": [
                    {"start": m.start(), "end": m.end(), "type": "PHONE"}
                    for m in PHONE.finditer(body["text"])
                ]
            }
        return super().handle(path, body)


class Desktop:
    def __init__(self, url):
        self.server = ServerClient(url, dev_token=True)
        self.base, self.ignore = config.load_base(), set()
        self.server.health()
        self.revisions = []

    def monitoring_active(self):
        return True

    def server_mode(self):
        return True


class SeenActions(BrowserActions):
    def preview(self, text):
        self.app.revisions.append(text)
        return super().preview(text)


def check_editor(context, peer, desktop, site, use_shell):
    page = context.new_page()
    html = (
        '<textarea id="prompt-textarea"></textarea>'
        if site == "chatgpt.com"
        else '<div class="ProseMirror" role="textbox" contenteditable="true"><p><br></p></div>'
    )
    page.route(
        "https://" + site + "/**",
        lambda route: route.fulfill(
            content_type="text/html",
            body=(
                '<meta charset="utf-8"><style>body{padding:80px}textarea,.ProseMirror{height:90px;width:540px;'
                "background:#eee;font:20px/1.5 monospace;padding:8px}button{margin-top:20px}</style>"
                + html
                + '<button id="outside">다른 곳</button>'
            ),
        ),
    )
    if use_shell:
        page.expose_function(
            "fixtureMessage",
            lambda m: {
                "value": peer(m["action"], **{k: v for k, v in m.items() if k not in ("action", "document")})
            },
        )
        # Native extension messaging is exercised by the normal packaged run; the shell uses the real desktop peer.
        page.add_init_script(
            "globalThis.chrome={runtime:{sendMessage:m=>fixtureMessage(m),onMessage:{addListener(){}}},storage:{onChanged:{addListener(){}}}};"
        )
    page.goto("https://" + site + "/fixture")
    if use_shell:
        for name in ("editor.js", "content.js"):
            page.add_script_tag(path=EXT / name)
    page.wait_for_selector("[data-kpguard-status]", state="attached")
    cdp = context.new_cdp_session(page)

    def elements(css):
        tree = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]

        def walk(node):
            if "data-kpguard-status" in node.get("attributes", []):
                return node["shadowRoots"][0]["nodeId"]
            for child in node.get("children", []):
                found = walk(child)
                if found:
                    return found

        scope = walk(tree)
        return cdp.send("DOM.querySelectorAll", {"nodeId": scope, "selector": css})["nodeIds"]

    def wait_for(css):
        import time

        end = time.monotonic() + 6
        while time.monotonic() < end:
            found = elements(css)
            if found:
                return found[0]
            page.wait_for_timeout(30)
        raise AssertionError(site + " did not show " + css + "; revisions=" + str(len(desktop.revisions)))

    def click(css):
        node = wait_for(css)
        coords = cdp.send("DOM.getBoxModel", {"nodeId": node})["model"]["content"]
        page.mouse.click((coords[0] + coords[4]) / 2, (coords[1] + coords[5]) / 2)

    field = page.locator("textarea,.ProseMirror").first
    field.click()
    page.keyboard.insert_text("🧪 연락 ")
    page.keyboard.type(PHONE_TEXT, delay=30)
    wait_for(".mark")
    assert len(desktop.revisions) > 1, "typing was not inspected until pause"
    assert not elements(".chip:not([hidden])")
    click(".mark")
    wait_for(".chip:not([hidden])")
    page.locator("#outside").click()
    assert not elements(".chip:not([hidden])")
    wait_for(".mark")
    click(".mark")
    click(".chip .primary")
    wait_for(".chip pre")
    click(".chip .primary")
    if site == "chatgpt.com":
        page.wait_for_function("!document.querySelector('textarea').value.includes('010-')")
        value = field.input_value()
    else:
        page.wait_for_function("!document.querySelector('.ProseMirror').innerText.includes('010-')")
        value = field.inner_text()
    assert "[전화번호_1]" in value, value
    print(site, "typing / persistent highlight / click / apply passed", flush=True)
    page.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-runtime", action="store_true")
    parser.add_argument("--chromium")
    parser.add_argument("--server", help="Optional local team backend with /process/preview")
    args = parser.parse_args()
    if args.server and urlsplit(args.server).hostname not in ("localhost", "127.0.0.1"):
        parser.error("Use a loopback test server")
    key = secrets.token_urlsafe(32)
    with ExitStack() as stack:
        url = args.server or stack.enter_context(FixtureBackend()).url
        desktop = Desktop(url)
        bridge = BrowserBridge(key, SeenActions(desktop))
        bridge.start()
        stack.callback(bridge.stop)
        peer = Peer(key)
        playwright = stack.enter_context(sync_playwright())
        flags = ["--disable-extensions-except=" + str(EXT), "--load-extension=" + str(EXT)]
        if args.fixture_runtime:
            flags += ["--no-zygote", "--single-process", "--disable-gpu"]
        profile = stack.enter_context(TemporaryDirectory())
        ctx = playwright.chromium.launch_persistent_context(
            profile,
            channel=None if args.chromium else "chromium",
            executable_path=args.chromium,
            headless=True,
            args=flags,
            ignore_default_args=["--disable-extensions"],
        )
        stack.callback(ctx.close)
        if not args.fixture_runtime:
            worker = (ctx.service_workers or [ctx.wait_for_event("serviceworker", timeout=15000)])[0]
            worker.evaluate("(key)=>chrome.storage.local.set({key})", key)
        for site in ("chatgpt.com", "claude.ai"):
            desktop.revisions.clear()
            check_editor(ctx, peer, desktop, site, args.fixture_runtime)


if __name__ == "__main__":
    main()
