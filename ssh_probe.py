"""Detached read-only probe of one pinned worktree after its SSH session ends."""
import fcntl
import json
import os
import pwd
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from work_session import load_autonomy_conf

# Deliberately not loaded at import time: importing this module must never
# touch autonomy.conf, so a missing or broken config cannot break the probe
# before it has logged anything. main() fills this in via load_conf(), which
# falls back to the hardcoded defaults when the file is missing or invalid.
# load_autonomy_conf is imported at module level so tests can patch the lookup
# source; load_conf() still catches its errors so a broken config or a broken
# work_session cannot kill the probe before START is logged.
_CONF = None

COMMIT = 'bd5600c602a8896dbfbcf6b185729ce44483d2e4'
BRANCH = 'development/real-map-data'

BASE = Path('/home/web/seaparser/autonomy')
WORKDIR = Path('/home/web/seaparser/worktrees/real-map-data')
SOCKET = BASE / 'tmux.sock'
SESSION = 'seaparser-autonomy-probe'
LOGDIR = BASE / 'logs/ssh-probe-bd5600c'


def conf_value(key, default, conf=None):
    """Look up KEY in autonomy.conf, warning on stderr when it is missing.

    Args:
        key: Config key name.
        default: Value used when the key is absent.
        conf: Config dict to read; defaults to the module-level _CONF.

    Returns:
        The configured value, or default.
    """
    values = _CONF if conf is None else conf
    if not values or key not in values:
        print(f'VARNING: {key} saknas i autonomy.conf, anvander default', file=sys.stderr)
        return default
    return values[key]


def load_conf(path=None):
    """Load autonomy.conf and derive the configurable module constants.

    A missing or unparsable config never aborts the probe: the error is
    reported on stderr and every constant keeps its hardcoded default, so a
    START/END/EXIT_CODE log is still written. The same holds when
    work_session itself cannot be imported.

    Args:
        path: Optional explicit config path; defaults to autonomy.conf next to
            work_session.py.

    Returns:
        The parsed config dict (empty when the file could not be read), also
        stored in the module-level _CONF.
    """
    global _CONF, BASE, WORKDIR, SOCKET, SESSION, LOGDIR
    try:
        _CONF = load_autonomy_conf(path)
    except (OSError, ValueError, ImportError) as error:
        print(f'VARNING: autonomy.conf kunde inte lasas ({error}), anvander default',
              file=sys.stderr)
        _CONF = {}
    BASE = Path(conf_value('BASE', str(BASE)))
    WORKDIR = Path(conf_value('WORKDIR', str(WORKDIR)))
    SOCKET = Path(conf_value('SOCKET', str(BASE / 'tmux.sock')))
    SESSION = conf_value('PROBE_SESSION', SESSION)
    LOGDIR = BASE / conf_value('LOGDIR', f'logs/ssh-probe-{COMMIT[:7]}')
    return _CONF


def utc():
    return datetime.now(timezone.utc).isoformat()


def tty_identity(path):
    try:
        stat = Path(path).stat()
        return [stat.st_dev, stat.st_ino, stat.st_rdev]
    except FileNotFoundError:
        return None


def project_status():
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(WORKDIR), *args],
                                       env=env, stdin=subprocess.DEVNULL, timeout=5).decode().strip()
    result = {'commit': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
              'local_changes': git('status', '--porcelain')}
    if result['commit'] != COMMIT or result['branch'] != BRANCH or result['local_changes']:
        raise ValueError('Expected pinned commit, branch and clean worktree; no files changed')
    return result


def disconnected(meta):
    # su - may clear SSH_* and its root-owned sshd ancestors may be unreadable.
    # Observe the inherited original PTY instead. Ending only the su shell or
    # the launcher cannot pass this check while the original SSH PTY remains.
    if tty_identity(meta['origin_tty']) != meta['origin_tty_identity']:
        return 'origin_terminal_closed'
    return None


def worker(directory, disconnect_check=disconnected):
    directory = Path(directory)
    meta = json.loads((directory / 'launch.json').read_text())
    result = dict(meta, started_at=utc(), exit_code=1, ssh_persistence_verified=False)
    remaining = min(120, (datetime.fromisoformat(meta['end_deadline']) - datetime.now(timezone.utc)).total_seconds())
    deadline = time.monotonic() + remaining
    with (directory / 'probe.log').open('a', buffering=1) as log:
        def emit(text):
            log.write(text + '\n')
        emit(f'START={result["started_at"]} CWD={WORKDIR} EXPECTED_COMMIT={COMMIT} MODE={meta["mode"]} END_DEADLINE={meta["end_deadline"]}')
        try:
            if pwd.getpwuid(os.getuid()).pw_name != 'seaparser':
                raise PermissionError('Worker must run as seaparser')
            with (BASE / 'run.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    result['exit_code'] = 75
                    raise ValueError('Another wrapper run holds run.lock')
                result['before_disconnect'] = project_status()
                emit('INITIAL_STATUS=' + json.dumps(result['before_disconnect']))
                (directory / 'started').write_text(result['started_at'] + '\n')
                while time.monotonic() < deadline - 25:
                    method = disconnect_check(meta)
                    if method:
                        result['disconnect_observed_at'] = utc()
                        result['disconnect_method'] = method
                        emit(f'SSH_DISCONNECT_OBSERVED={result["disconnect_observed_at"]} METHOD={method}')
                        # Work performed after the original PuTTY/SSH terminal has closed.
                        time.sleep(5)
                        result['after_disconnect'] = project_status()
                        emit('AFTER_DISCONNECT_STATUS=' + json.dumps(result['after_disconnect']))
                        result['ssh_persistence_verified'] = meta['mode'] == 'ssh-disconnect-proof'
                        result['exit_code'] = 0
                        break
                    time.sleep(0.25)
                else:
                    result['exit_code'] = 124
                    raise TimeoutError('SSH disconnect not observed before the deadline')
        except Exception as error:
            result['error_type'] = type(error).__name__
            emit('FAILED=' + type(error).__name__)
        finally:
            result['finished_at'] = utc()
            emit(f'END={result["finished_at"]} EXIT_CODE={result["exit_code"]} SSH_PERSISTENCE_VERIFIED={int(result["ssh_persistence_verified"])}')
            (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            (directory / 'exit-code').write_text(str(result['exit_code']) + '\n')
    return result['exit_code']


def launch():
    if pwd.getpwuid(os.getuid()).pw_name != 'seaparser':
        raise ValueError('Run as seaparser, including through su - seaparser')
    if not os.isatty(0):
        raise ValueError('An interactive PuTTY/SSH terminal is required')
    tty = os.ttyname(0)
    if not tty.startswith('/dev/pts/') or os.environ.get('TMUX') or os.environ.get('STY'):
        raise ValueError('Run in the original PuTTY terminal, outside tmux/screen or su --pty')
    identity = tty_identity(tty)
    stdin = os.fstat(0)
    if identity is None or identity != [stdin.st_dev, stdin.st_ino, stdin.st_rdev]:
        raise ValueError('Original terminal must be observable')
    project_status()
    LOGDIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (BASE / 'ssh-probe-launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = LOGDIR / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + f'-{os.getpid()}')
        directory.mkdir(mode=0o700)
        meta = {'mode': 'ssh-disconnect-proof', 'workdir': str(WORKDIR), 'expected_commit': COMMIT,
                'run_user': 'seaparser', 'launch_context': 'user-confirmed PuTTY SSH via su -',
                'origin_tty': tty, 'origin_tty_identity': identity, 'launched_at': utc(),
                'end_deadline': (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
                'tmux_socket': str(SOCKET), 'tmux_session': SESSION}
        (directory / 'launch.json').write_text(json.dumps(meta, indent=2) + '\n')
        command = shlex.join(['/usr/bin/python3', '-B', str(BASE / 'ssh_probe.py'), '--worker', str(directory)])
        started = subprocess.run(['/usr/bin/tmux', '-S', str(SOCKET), 'new-session', '-d',
                                  '-s', SESSION, '-c', str(WORKDIR), command],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        (directory / 'launcher.log').write_text(started.stdout + started.stderr)
        if started.returncode:
            print(f'TMUX_START_FAILED={started.returncode}; LOG={directory / "launcher.log"}', flush=True)
            return started.returncode
        for name, target in (('latest.log', directory / 'probe.log'), ('latest.json', directory / 'result.json')):
            temporary = LOGDIR / (name + '.tmp')
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(target)
            temporary.replace(LOGDIR / name)
        for _ in range(20):
            if (directory / 'started').exists():
                print(f'PROBE_STARTED COMMIT={COMMIT}\nLOG={LOGDIR / "latest.log"}\nRESULT={LOGDIR / "latest.json"}\nStäng PuTTY manuellt inom 60 sekunder. SSH-sessionen lämnas öppen av startaren.', flush=True)
                return 0
            if (directory / 'result.json').exists():
                print(f'PROBE_START_FAILED; LOG={directory / "probe.log"}', flush=True)
                return 1
            time.sleep(0.25)
        print(f'PROBE_START_UNCONFIRMED; LOG={directory / "probe.log"}', flush=True)
        return 1


def main(argv=None):
    """Load autonomy.conf, then run the worker or the launcher.

    Args:
        argv: Argument list; defaults to sys.argv.

    Returns:
        Process exit code.
    """
    argv = sys.argv if argv is None else argv
    load_conf()
    if len(argv) == 3 and argv[1] == '--worker':
        return worker(argv[2])
    return launch()


if __name__ == '__main__':
    os.umask(0o077)
    try:
        code = main()
    except Exception as error:
        print('PROBE_FAILED: ' + str(error), file=sys.stderr)
        code = 1
    raise SystemExit(code)
