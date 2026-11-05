import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent.system import autostart

class MacAutostartTests(unittest.TestCase):
    def test_enable_and_disable_register_current_command(self):
        with tempfile.TemporaryDirectory() as d, patch.object(autostart.sys, 'platform', 'darwin'), patch.object(autostart, '_launch_agent_path', return_value=Path(d) / 'agent.plist'):
            self.assertFalse(autostart.is_enabled())
            autostart.enable()
            self.assertTrue(autostart.is_enabled())
            self.assertEqual(plistlib.loads((Path(d) / 'agent.plist').read_bytes())['ProgramArguments'], autostart._mac_arguments())
            autostart.disable()
            self.assertFalse(autostart.is_enabled())

    def test_stale_registration_is_not_enabled(self):
        with tempfile.TemporaryDirectory() as d, patch.object(autostart.sys, 'platform', 'darwin'), patch.object(autostart, '_launch_agent_path', return_value=Path(d) / 'agent.plist'):
            (Path(d) / 'agent.plist').write_bytes(plistlib.dumps({'ProgramArguments': ['/missing/app']}))
            self.assertFalse(autostart.is_enabled())
