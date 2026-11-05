"""macOS status item; created and updated on the Tk main thread."""
try:
    from AppKit import NSStatusBar, NSVariableStatusItemLength, NSMenu, NSMenuItem
    from Foundation import NSObject
except ImportError:
    NSObject = None


def available():
    return NSObject is not None


if NSObject is not None:
    class _Actions(NSObject):
        def initWithApp_(self, app):
            self = super().init()
            if self is not None:
                self.app = app
            return self

        def invoke_(self, sender):
            self.app.post(str(sender.representedObject()))


class Tray:
    def __init__(self, app):
        self.app = app
        self.actions = _Actions.alloc().initWithApp_(app)
        self.item = None

    def start(self):
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        menu = NSMenu.alloc().init()
        for title, command in [('프롬프트 점검', 'review'), ('답변 복원', 'restore'),
                               ('파일 검사', 'files'), ('감시 켜기/끄기', 'toggle'),
                               ('30분 일시정지', 'pause'), ('설정', 'settings'), ('종료', 'quit')]:
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, 'invoke:', '')
            entry.setTarget_(self.actions)
            entry.setRepresentedObject_(command)
            menu.addItem_(entry)
        self.item.setMenu_(menu)
        self.refresh()

    def refresh(self):
        if self.item is not None:
            label = {'on': 'KP ●', 'paused': 'KP ◐', 'off': 'KP ○'}[self.app.state()]
            self.item.button().setTitle_(label)

    def notify(self, message):
        pass

    def stop(self):
        if self.item is not None:
            NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
            self.item = None
