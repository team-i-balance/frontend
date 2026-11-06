"""Browser extension regression on claude.ai: typing, click-away and editor replacement.

Scope (2026-11-05): Claude in the browser only. Run with Playwright Chromium installed.
--fixture-runtime also supports a headless-shell binary.
The fixture never contacts Claude or a production team server.
"""

import argparse
import re
import secrets
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import Error, sync_playwright

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
    def __init__(self, url, backend=None):
        self.backend = backend
        self.server = ServerClient(url, dev_token=True)
        self.base, self.ignore = config.load_base(), set()
        self.server.health()
        self.revisions = []

    def monitoring_active(self):
        return True

    def server_mode(self):
        return True


class SeenActions(BrowserActions):
    def preview(self, text, channel=None, target=None):
        self.app.revisions.append(text)
        return super().preview(text, channel, target)


def check_editor(context, peer, desktop, site, use_shell):
    page = context.new_page()
    html = '<div class="ProseMirror" role="textbox" contenteditable="true"><p><br></p></div>'
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
        peer("reset")  # the shell shares one desktop channel across both sites
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

    def chips():
        out = []
        try:
            for node_id in elements(".chip [data-chip]"):
                attrs = cdp.send("DOM.getAttributes", {"nodeId": node_id})["attributes"]
                out.append(dict(zip(attrs[::2], attrs[1::2]))["data-chip"])
        except Error:  # the chip re-rendered between two CDP calls
            return []
        return out

    def text_of(css):
        try:
            html = cdp.send("DOM.getOuterHTML", {"nodeId": wait_for(css)})["outerHTML"]
        except Error:
            return ""
        return re.sub(r"<[^>]+>", "", html)

    def current():
        return field.input_value() if site == "chatgpt.com" else field.inner_text()

    def until(test, what):
        import time

        end = time.monotonic() + 6
        while time.monotonic() < end:
            if test():
                return
            page.wait_for_timeout(30)
        raise AssertionError(site + " " + what + "; value=" + repr(current()) + " chips=" + str(chips()))

    field = page.locator("textarea,.ProseMirror").first
    field.click()
    page.keyboard.insert_text("🧪 연락 ")
    page.keyboard.type(PHONE_TEXT, delay=30)
    wait_for(".mark")
    assert len(desktop.revisions) > 1, "typing was not inspected until pause"
    assert not elements(".chip:not([hidden])")
    # 하이라이트가 남아 있으면 Enter(전송)를 막고 첫 항목의 칩을 연다
    before = current()
    page.keyboard.press("Enter")
    wait_for(".chip:not([hidden])")
    assert current() == before, "Enter went through with a highlight"
    assert chips() == ["mask", "keep"], chips()
    page.locator("#outside").click()
    assert not elements(".chip:not([hidden])")
    wait_for(".mark")
    # 마스킹 → 바뀔 값만 → 적용: 입력창의 그 자리만 바뀐다
    click(".mark")
    wait_for('.chip [data-chip="mask"]')
    assert chips() == ["mask", "keep"], chips()
    click('.chip [data-chip="mask"]')
    until(lambda: "apply" in chips() and "[전화번호_1]" in text_of(".chip .word"), "masked word not shown")
    click('.chip [data-chip="apply"]')
    until(lambda: current().replace("\n", "") == "🧪 연락 [전화번호_1]", "value not edited in place")
    wait_for('.chip [data-chip="undo"]')
    until(lambda: not elements(".mark"), "highlight stayed after masking")
    if desktop.backend:
        until(lambda: any(d["action"] == "send_transformed" for d in desktop.backend.decisions), "decision not recorded")
    # 원문유지 → 사유 셋
    page.keyboard.insert_text(" 010-9876-5432")
    wait_for(".mark")
    click(".mark")
    click('.chip [data-chip="keep"]')
    until(lambda: chips() == ["back", "reason-business_need", "reason-false_positive", "reason-approved"], "reasons")
    click('.chip [data-chip="reason-business_need"]')
    until(lambda: not elements(".mark"), "kept value still highlighted")
    wait_for('.chip [data-chip="undo"]')
    if desktop.backend:
        until(lambda: any(d["action"] == "send_as_is" and d["reason"] == "business_need"
                          for d in desktop.backend.decisions), "keep not recorded")
    page.keyboard.press("Enter")  # 하이라이트가 없으면 막지 않는다
    until(lambda: "\n" in current().strip("\n") or current().endswith("\n"), "Enter blocked without highlights")
    print(site, "typing / Enter blocked / mask in place / keep with reason passed", flush=True)
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
        backend = None if args.server else stack.enter_context(FixtureBackend())
        url = args.server or backend.url
        desktop = Desktop(url, backend)
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
        check_editor(ctx, peer, desktop, "claude.ai", args.fixture_runtime)


if __name__ == "__main__":
    main()
