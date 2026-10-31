from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import tomlkit

import wechat_reader_setup as setup


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.toml'
        self.python = Path(self.directory.name) / 'venv with spaces' / 'python'

    def test_first_install_and_repeated_install(self):
        self.assertIsNone(setup.configure_codex(self.path, self.python))
        server = tomlkit.parse(self.path.read_text())['mcp_servers']['wechat-reader']
        self.assertEqual(server['command'], str(self.python))
        self.assertEqual(server['args'], ['-m', 'wechat_reader_server'])
        before = self.path.read_bytes()
        self.assertIsNone(setup.configure_codex(self.path, self.python))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('config.toml.backup-*')), [])

    def test_preserves_other_settings_comments_and_server_policies(self):
        original = ('# My settings\nmodel = "example"\n'
                    '[mcp_servers.other]\ncommand = "keep"\n'
                    '[mcp_servers.wechat-reader]\ncommand = "old"\nargs = ["old.py"]\n'
                    'enabled = false\ntool_timeout_sec = 90\n'
                    '[mcp_servers.wechat-reader.tools.read_wechat_article]\napproval_mode = "prompt"\n')
        self.path.write_text(original, encoding='utf-8')
        backup = setup.configure_codex(self.path, self.python)
        self.assertEqual(backup.read_text(encoding='utf-8'), original)
        after = self.path.read_text(encoding='utf-8')
        config = tomlkit.parse(after)
        self.assertTrue(after.startswith('# My settings\n'))
        self.assertEqual(config['model'], 'example')
        self.assertEqual(config['mcp_servers']['other']['command'], 'keep')
        server = config['mcp_servers']['wechat-reader']
        self.assertTrue(server['enabled'])
        self.assertEqual(server['tool_timeout_sec'], 90)
        self.assertEqual(server['tools']['read_wechat_article']['approval_mode'], 'prompt')
        self.assertIsNone(setup.configure_codex(self.path, self.python))
        self.assertEqual(len(list(self.path.parent.glob('config.toml.backup-*'))), 1)

    def test_invalid_config_and_remote_conflict_are_untouched(self):
        for text in ['invalid = [', '[mcp_servers.wechat-reader]\nurl = "https://example.com/mcp"\n']:
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(Exception):
                    setup.configure_codex(self.path, self.python)
                self.assertEqual(self.path.read_text(), text)
                self.assertEqual(list(self.path.parent.glob('config.toml.backup-*')), [])

    @unittest.skipIf(os.name == 'nt', 'Symlink creation may require Windows privileges')
    def test_venv_python_symlink_is_not_resolved_to_base_python(self):
        self.python.parent.mkdir()
        self.python.symlink_to(sys.executable)
        setup.configure_codex(self.path, self.python)
        configured = tomlkit.parse(self.path.read_text())['mcp_servers']['wechat-reader']['command']
        self.assertEqual(configured, str(self.python))

    def test_failed_replace_preserves_original_and_cleans_temporary_file(self):
        self.path.write_text('model = "keep"\n')
        with patch.object(Path, 'replace', side_effect=OSError('write denied')):
            with self.assertRaises(OSError):
                setup.configure_codex(self.path, self.python)
        self.assertEqual(self.path.read_text(), 'model = "keep"\n')
        self.assertEqual(list(self.path.parent.glob('.wechat-reader-config-*')), [])


class DoctorTests(unittest.TestCase):
    def test_missing_chrome_installs_browser_before_configuring(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sys, 'argv', ['setup']), \
                patch.object(setup, 'config_path', return_value=Path(directory) / 'config.toml'), \
                patch.object(setup, 'browser_path', side_effect=[None, Path('chromium')]), \
                patch.object(setup, 'verify_browser') as verify, \
                patch.object(setup, 'verify_mcp', new_callable=AsyncMock), \
                patch.object(setup.subprocess, 'run') as install, \
                redirect_stdout(StringIO()):
            self.assertEqual(setup.main(), 0)
            install.assert_called_once_with([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=True)
            verify.assert_called_once_with(Path('chromium'))

    def test_check_is_read_only_and_validates_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            setup.configure_codex(path, Path(sys.executable))
            original = path.read_bytes()
            with patch.object(sys, 'argv', ['setup', '--check']), \
                    patch.object(setup, 'config_path', return_value=path), \
                    patch.object(setup, 'browser_path', return_value=Path('fake-chrome')), \
                    patch.object(setup, 'verify_browser'), \
                    patch.object(setup, 'verify_mcp', new_callable=AsyncMock), \
                    patch.object(setup, 'configure_codex') as configure, \
                    patch.object(setup.subprocess, 'run') as install, \
                    redirect_stdout(StringIO()):
                self.assertEqual(setup.main(), 0)
                configure.assert_not_called()
                install.assert_not_called()
            self.assertEqual(path.read_bytes(), original)

    def test_missing_browser_check_does_not_download_or_configure(self):
        with patch.object(sys, 'argv', ['setup', '--check']), \
                patch.object(setup, 'browser_path', return_value=None), \
                patch.object(setup, 'configure_codex') as configure, \
                patch.object(setup.subprocess, 'run') as install, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(setup.main(), 1)
            install.assert_not_called()
            configure.assert_not_called()

    def test_failed_browser_install_does_not_change_codex(self):
        with patch.object(sys, 'argv', ['setup']), \
                patch.object(setup, 'browser_path', return_value=None), \
                patch.object(setup, 'configure_codex') as configure, \
                patch.object(setup.subprocess, 'run', side_effect=RuntimeError('network unavailable')), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(setup.main(), 1)
            configure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
