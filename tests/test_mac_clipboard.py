import unittest
from unittest.mock import patch
from agent.system import mac_clipboard as clip

class Board:
    count = 0
    text = None
    def changeCount(self): return self.count
    def stringForType_(self, kind): return self.text
    def clearContents(self): self.text = None; self.count += 1
    def setString_forType_(self, text, kind): self.text = text; return True

class MacClipboardTests(unittest.TestCase):
    def test_sequence_and_copy(self):
        board = Board()
        with patch.object(clip, 'NSPasteboard') as api, patch.object(clip, 'NSPasteboardTypeString', 'text', create=True):
            api.generalPasteboard.return_value = board
            self.assertEqual(clip.clipboard_sequence(), 0)
            self.assertTrue(clip.set_clipboard_text('가상 입력'))
            self.assertEqual(clip.get_clipboard_text(), '가상 입력')
            self.assertEqual(clip.clipboard_sequence(), 1)
