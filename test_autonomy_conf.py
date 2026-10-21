"""Unittest coverage for the autonomy.conf helpers in work_session."""
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import work_session
from work_session import (check_trusted_path, conf_required, conf_value,
                          ensure_private_dir, load_autonomy_conf, reload_conf,
                          verify_trusted_config)


class TempConfMixin:
    """Helpers that create temporary conf files and private temp directories."""

    def make_conf(self, text):
        """Write text to a fresh temporary conf file and return its path.

        Args:
            text: Raw contents of the config file.

        Returns:
            Path to the created file; it is removed when the test ends.
        """
        handle = tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False)
        with handle:
            handle.write(text)
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def make_dir(self):
        """Create a fresh temporary directory that is removed when the test ends.

        Returns:
            Path to the created directory.
        """
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        return directory


class LoadAutonomyConfTests(TempConfMixin, unittest.TestCase):
    """Each test writes its own temporary conf file and removes it afterwards."""

    def test_valid_conf_parses(self):
        """Two KEY=value lines parse into the expected dict, skipping comments/blanks."""
        path = self.make_conf('# kommentar\n\nMODEL=gpt-6.1-sol\nHEAD=abc123\n')
        self.assertEqual(load_autonomy_conf(path),
                         {'MODEL': 'gpt-6.1-sol', 'HEAD': 'abc123'})

    def test_rejects_spaces_in_value(self):
        """A value containing a space is rejected."""
        path = self.make_conf('KEY=has space\n')
        with self.assertRaises(ValueError):
            load_autonomy_conf(path)

    def test_rejects_line_without_equals(self):
        """A line without '=' is rejected."""
        path = self.make_conf('JUSTAKEY\n')
        with self.assertRaises(ValueError):
            load_autonomy_conf(path)

    def test_rejects_quoted_value(self):
        """A quoted value without whitespace is still rejected by the strict format."""
        path = self.make_conf('MODEL="gpt-6.1-sol"\n')
        with self.assertRaises(ValueError):
            load_autonomy_conf(path)


class ConfAccessorTests(TempConfMixin, unittest.TestCase):
    """conf_required/conf_value/reload_conf must never silently use a default."""

    def patch_conf(self, path):
        """Point the module-level CONF_PATH at path and reset the cache.

        Args:
            path: Path to the temporary conf file to use.
        """
        patcher = mock.patch.object(work_session, 'CONF_PATH', Path(path))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, work_session, '_CONF', None)
        work_session._CONF = None

    def test_conf_required_returns_value(self):
        """A present key is returned verbatim."""
        self.patch_conf(self.make_conf('HEAD=abc123\n'))
        self.assertEqual(conf_required('HEAD'), 'abc123')

    def test_conf_required_refuses_missing_key(self):
        """A missing mandatory key raises instead of falling back to a default."""
        self.patch_conf(self.make_conf('HEAD=abc123\n'))
        with self.assertRaises(ValueError):
            conf_required('SSH_HEAD')

    def test_conf_value_warns_on_missing_key(self):
        """A missing optional key returns the default and warns on stderr."""
        self.patch_conf(self.make_conf('HEAD=abc123\n'))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(conf_value('MODEL', 'fallback'), 'fallback')
        self.assertIn('MODEL', stderr.getvalue())

    def test_conf_value_returns_configured_value(self):
        """A present optional key wins over the default without warning."""
        self.patch_conf(self.make_conf('MODEL=gpt-6.1-sol\n'))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(conf_value('MODEL', 'fallback'), 'gpt-6.1-sol')
        self.assertEqual(stderr.getvalue(), '')

    def test_reload_conf_invalidates_cache(self):
        """reload_conf re-reads the file after it changed on disk."""
        path = self.make_conf('MODEL=first\n')
        self.patch_conf(path)
        self.assertEqual(conf_value('MODEL', 'fallback'), 'first')
        path.write_text('MODEL=second\n')
        self.assertEqual(conf_value('MODEL', 'fallback'), 'first')
        self.assertEqual(reload_conf()['MODEL'], 'second')
        self.assertEqual(conf_value('MODEL', 'fallback'), 'second')


class TrustedPathTests(TempConfMixin, unittest.TestCase):
    """check_trusted_path/ensure_private_dir/verify_trusted_config fail closed."""

    def test_accepts_private_file(self):
        """A file owned by us with private mode passes."""
        path = self.make_conf('KEY=value\n')
        path.chmod(0o600)
        check_trusted_path(path, 'conf')

    def test_rejects_missing_path(self):
        """A missing path raises ValueError."""
        with self.assertRaises(ValueError):
            check_trusted_path(Path(tempfile.gettempdir())/'no-such-autonomy-conf', 'conf')

    def test_rejects_group_or_world_writable_file(self):
        """A group- or world-writable file raises ValueError."""
        path = self.make_conf('KEY=value\n')
        for mode in (0o620, 0o602, 0o666):
            path.chmod(mode)
            with self.assertRaises(ValueError):
                check_trusted_path(path, 'conf')

    def test_rejects_foreign_owner(self):
        """A file owned by another uid raises ValueError."""
        path = self.make_conf('KEY=value\n')
        path.chmod(0o600)
        with mock.patch.object(work_session.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(ValueError):
                check_trusted_path(path, 'conf')

    def test_ensure_private_dir_creates_private_directory(self):
        """A missing directory is created with mode 0700 and then accepted."""
        target = self.make_dir()/'nested'/'logs'
        ensure_private_dir(target, 'LOGROOT')
        self.assertTrue(target.is_dir())
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_ensure_private_dir_rejects_world_writable_directory(self):
        """An existing group/world-writable directory is rejected."""
        target = self.make_dir()
        target.chmod(0o777)
        with self.assertRaises(ValueError):
            ensure_private_dir(target, 'LOGROOT')

    def test_verify_trusted_config_rejects_untrusted_conf(self):
        """verify_trusted_config fails closed when autonomy.conf is world-writable."""
        path = self.make_conf('KEY=value\n')
        path.chmod(0o666)
        with mock.patch.object(work_session, 'CONF_PATH', path):
            with self.assertRaises(ValueError):
                verify_trusted_config()

    def test_verify_trusted_config_accepts_trusted_paths(self):
        """verify_trusted_config passes when every trusted path is private."""
        root = self.make_dir()
        conf = self.make_conf('KEY=value\n')
        conf.chmod(0o600)
        codex = root/'codex'
        codex.write_text('#!/bin/sh\n')
        codex.chmod(0o700)
        socket_dir = root/'sock'
        socket_dir.mkdir(mode=0o700)
        with mock.patch.multiple(work_session, CONF_PATH=conf, CODEX=codex,
                                 SOCKET=socket_dir/'tmux.sock',
                                 PROBE_ROOT=root/'probe', LOGROOT=root/'logs'):
            verify_trusted_config()
        self.assertTrue((root/'probe').is_dir())
        self.assertTrue((root/'logs').is_dir())


if __name__ == '__main__':
    unittest.main()
