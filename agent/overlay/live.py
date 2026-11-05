"""One in-flight check and the latest draft. Typing never resets an idle timer."""

import threading
import time


def stable_ranges(spans, old, new):
    if old == new:
        return spans
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    delta = len(new) - len(old)
    return [
        s if s["end"] < prefix else {**s, "start": s["start"] + delta, "end": s["end"] + delta}
        for s in spans
        if s["end"] < prefix or s["start"] > len(old) - suffix
    ]


class LiveRanges:
    def __init__(self, preview):
        self.preview = preview
        self.condition = threading.Condition()
        self.key = None
        self.epoch = 0
        self.text = ""
        self.checked = None
        self.spans = []
        self.retry_at = 0
        self.error = False
        self.closed = False

    def update(self, key, text=""):
        with self.condition:
            if key != self.key:
                self.epoch += 1
                self.key, self.spans, self.checked = key, [], None
                self.retry_at, self.error = 0, False
            if text != self.text:
                self.spans = stable_ranges(self.spans, self.text, text)
                self.text = text
                self.checked = None
            self.condition.notify()
            return list(self.spans)

    def close(self):
        with self.condition:
            self.closed = True
            self.text, self.spans, self.key = "", [], None
            self.condition.notify()

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(
                    lambda: (
                        self.closed
                        or (
                            self.key is not None
                            and self.text.strip()
                            and self.checked != self.text
                            and time.monotonic() >= self.retry_at
                        )
                    )
                )
                if self.closed:
                    return
                text, epoch = self.text, self.epoch
            try:
                spans = self.preview(text)["spans"]
            except Exception:  # Network/permissions can recover while the same draft remains open.
                with self.condition:
                    if epoch == self.epoch:
                        self.retry_at, self.error = time.monotonic() + 2, True
                continue
            with self.condition:
                if self.closed or epoch != self.epoch:
                    continue
                self.spans = stable_ranges(spans, text, self.text)
                self.checked, self.error = text, False
