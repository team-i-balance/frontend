import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from agent.gui.file_window import FileWindow

class FileSaveSafetyTests(unittest.TestCase):
    def test_refuses_source_hardlink_and_other_extensions(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / '고객 명단.xlsx'
            source.write_bytes(b'original')
            alias = Path(d) / '별칭.xlsx'
            os.link(source, alias)
            obj = SimpleNamespace(source=source, report=SimpleNamespace(file_bytes=b'protected'), app=SimpleNamespace(toast=lambda *_, **__: None))
            self.assertFalse(FileWindow.save_to(obj, alias))
            self.assertFalse(FileWindow.save_to(obj, Path(d) / 'out.txt'))
            self.assertEqual(source.read_bytes(), b'original')
