"""Tests for the autonomy.conf helpers and the trusted-path checks."""
import os
from pathlib import Path

import pytest

import work_session


def write_conf(tmp_path, text):
    """Write a config file and return its path."""
    path = tmp_path / 'autonomy.conf'
    path.write_text(text)
    return path


def test_load_autonomy_conf_parses_keys(tmp_path):
    """Plain KEY=value lines are parsed and comments/blank lines skipped."""
    path = write_conf(tmp_path, '# comment\n\nMODEL=gpt-6.1-sol\nHEAD=abc\n')
    assert work_session.load_autonomy_conf(path) == {'MODEL': 'gpt-6.1-sol', 'HEAD': 'abc'}


def test_load_autonomy_conf_rejects_quoted_value(tmp_path):
    """A quoted value does not match the strict line format."""
    path = write_conf(tmp_path, 'MODEL="gpt-6.1-sol"\n')
    with pytest.raises(ValueError):
        work_session.load_autonomy_conf(path)


def test_conf_required_refuses_to_guess(tmp_path, monkeypatch):
    """conf_required raises instead of falling back to a default."""
    monkeypatch.setattr(work_session, '_CONF', {'MODEL': 'x'})
    assert work_session.conf_required('MODEL') == 'x'
    with pytest.raises(ValueError):
        work_session.conf_required('HEAD')


def test_conf_value_warns_and_returns_default(monkeypatch, capsys):
    """A missing key yields the default plus a warning on stderr."""
    monkeypatch.setattr(work_session, '_CONF', {})
    assert work_session.conf_value('MODEL', 'fallback') == 'fallback'
    assert 'VARNING: MODEL saknas' in capsys.readouterr().err


def test_check_trusted_path_accepts_private_file(tmp_path):
    """A file owned by us with mode 0600 passes."""
    path = tmp_path / 'f'
    path.write_text('x')
    path.chmod(0o600)
    work_session.check_trusted_path(path, 'F')


def test_check_trusted_path_rejects_group_writable(tmp_path):
    """A group-writable file is rejected."""
    path = tmp_path / 'f'
    path.write_text('x')
    path.chmod(0o620)
    with pytest.raises(ValueError):
        work_session.check_trusted_path(path, 'F')


def test_check_trusted_path_rejects_world_writable(tmp_path):
    """A world-writable file is rejected."""
    path = tmp_path / 'f'
    path.write_text('x')
    path.chmod(0o602)
    with pytest.raises(ValueError):
        work_session.check_trusted_path(path, 'F')


def test_check_trusted_path_rejects_missing(tmp_path):
    """A missing path fails closed."""
    with pytest.raises(ValueError):
        work_session.check_trusted_path(tmp_path / 'nope', 'F')


def test_check_trusted_path_rejects_other_owner(tmp_path, monkeypatch):
    """A path owned by another uid is rejected."""
    path = tmp_path / 'f'
    path.write_text('x')
    path.chmod(0o600)
    real = os.getuid()
    monkeypatch.setattr(work_session.os, 'getuid', lambda: real + 1)
    with pytest.raises(ValueError):
        work_session.check_trusted_path(path, 'F')


def test_ensure_private_dir_creates_missing(tmp_path):
    """A missing directory is created with private permissions."""
    target = tmp_path / 'a' / 'b'
    work_session.ensure_private_dir(target, 'LOGROOT')
    assert target.is_dir()
    assert target.stat().st_mode & 0o077 == 0


def test_verify_trusted_config_fails_closed(tmp_path, monkeypatch):
    """verify_trusted_config propagates a failing trusted path."""
    conf = write_conf(tmp_path, 'MODEL=x\n')
    conf.chmod(0o666)
    monkeypatch.setattr(work_session, 'CONF_PATH', conf)
    with pytest.raises(ValueError):
        work_session.verify_trusted_config()


def test_verify_trusted_config_creates_roots(tmp_path, monkeypatch):
    """Missing PROBE_ROOT/LOGROOT are created instead of blocking the first run."""
    conf = write_conf(tmp_path, 'MODEL=x\n')
    conf.chmod(0o600)
    codex = tmp_path / 'codex'
    codex.write_text('')
    codex.chmod(0o700)
    sockdir = tmp_path / 'sock'
    sockdir.mkdir(mode=0o700)
    monkeypatch.setattr(work_session, 'CONF_PATH', conf)
    monkeypatch.setattr(work_session, 'CODEX', str(codex))
    monkeypatch.setattr(work_session, 'SOCKET', sockdir / 'tmux.sock')
    monkeypatch.setattr(work_session, 'PROBE_ROOT', tmp_path / 'probe')
    monkeypatch.setattr(work_session, 'LOGROOT', tmp_path / 'logs')
    work_session.verify_trusted_config()
    assert (tmp_path / 'probe').is_dir()
    assert (tmp_path / 'logs').is_dir()


def test_reload_conf_rereads_file(tmp_path, monkeypatch):
    """reload_conf drops the cache so edits become visible."""
    path = write_conf(tmp_path, 'MODEL=a\n')
    monkeypatch.setattr(work_session, 'CONF_PATH', path)
    monkeypatch.setattr(work_session, '_CONF', None)
    assert work_session.conf()['MODEL'] == 'a'
    path.write_text('MODEL=b\n')
    assert work_session.conf()['MODEL'] == 'a'
    assert work_session.reload_conf()['MODEL'] == 'b'
