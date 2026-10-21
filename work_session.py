"""Prepare/start/stop a serial five-hour Codex handoff through verified tmux."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

CONF_LINE = re.compile(r'^[A-Z_]+=[^\s"\']+$')


def load_autonomy_conf(path=None):
    """Parse autonomy.conf into a dict of KEY -> raw string value.

    Blank lines and lines starting with '#' are skipped. Every remaining line
    must match ^[A-Z_]+=[^\\s"']+$ and is split on its first '='.

    Args:
        path: Optional path to the config file; defaults to autonomy.conf
            next to this module.

    Returns:
        Dict mapping each KEY to its value as a string.

    Raises:
        ValueError: If a non-comment line does not match the expected format.
    """
    path = Path(path) if path is not None else Path(__file__).parent / 'autonomy.conf'
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line or line.startswith('#'):
            continue
        if not CONF_LINE.match(line):
            raise ValueError(f'{path}:{number}: invalid autonomy.conf line {line!r}; '
                             'expected KEY=value with no quotes or whitespace')
        key, value = line.split('=', 1)
        values[key] = value
    return values


CONF_PATH = Path(__file__).parent / 'autonomy.conf'
# Deliberately not loaded at import time: importing this module must never
# depend on the contents or trust state of autonomy.conf. conf() fills this in
# lazily on first use.
_CONF = None


def conf():
    """Return the parsed autonomy.conf, loading it lazily on first use.

    The file is read once per process and cached in the module global _CONF;
    edits to autonomy.conf during a running pass are deliberately ignored so
    that a pass cannot change its own pins mid-flight. Use reload_conf() to
    pick up changes explicitly.

    Returns:
        Dict mapping each KEY to its raw string value.
    """
    global _CONF
    if _CONF is None:
        _CONF = load_autonomy_conf(CONF_PATH)
    return _CONF


def reload_conf():
    """Drop the cached config so the next conf() call re-reads autonomy.conf.

    Returns:
        The freshly parsed config dict.
    """
    global _CONF
    _CONF = None
    return conf()


def conf_value(key, default):
    """Look up KEY in autonomy.conf, warning on stderr when it is missing.

    Used for values that have a safe built-in default. The module-level
    constants (MODEL, REASONING, CODEX, SOCKET, SESSION, PROBE_ROOT, LOGROOT)
    are resolved once at import time from the same cached config; the
    security-sensitive paths among them (CODEX, SOCKET, PROBE_ROOT, LOGROOT)
    only accept their default because verify_trusted_config() then enforces
    ownership and mode on the resolved path before any launch.

    Args:
        key: Config key name.
        default: Value used when the key is absent.

    Returns:
        The configured value, or default.
    """
    values = conf()
    if key not in values:
        print(f'VARNING: {key} saknas i autonomy.conf, anvander default', file=sys.stderr)
        return default
    return values[key]


def conf_required(key):
    """Return a mandatory config value, refusing to fall back to a default.

    Args:
        key: Config key name.

    Returns:
        The configured value.

    Raises:
        ValueError: If the key is missing from autonomy.conf.
    """
    values = conf()
    if key not in values:
        raise ValueError(f'{key} saknas i autonomy.conf; vagra gissa en pin')
    return values[key]


def current_uid():
    """Return the uid of the running process.

    Wrapped in a module-level function so tests can patch the lookup without
    mutating the shared os module process-wide.

    Returns:
        The effective uid as an int.
    """
    return os.getuid()


def check_trusted_path(path, label):
    """Fail closed unless path is owned by the run user and not group/world-writable.

    Args:
        path: File or directory to inspect.
        label: Name used in the error message.

    Raises:
        ValueError: If the path is missing, has another owner, or is
            group/world-writable.
    """
    path = Path(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ValueError(f'{label} saknas: {path}') from None
    if info.st_uid != os.getuid():
        raise ValueError(f'{label} ags inte av korande anvandare: {path}')
    if info.st_mode & 0o022:
        raise ValueError(f'{label} ar grupp-/varldsskrivbar: {path}')


def ensure_private_dir(path, label):
    """Create path with mode 0700 if missing, then verify it is trusted.

    The first run of a fresh checkout has no PROBE_ROOT/LOGROOT yet, so the
    directory is created under umask 077 before the ownership/mode check.

    Args:
        path: Directory to create and verify.
        label: Name used in error messages.

    Raises:
        ValueError: If the directory cannot be created or fails the check.
    """
    path = Path(path)
    previous = os.umask(0o077)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    finally:
        os.umask(previous)
    check_trusted_path(path, label)


def verify_trusted_config():
    """Verify ownership/mode of autonomy.conf and every security-sensitive path.

    PROBE_ROOT and LOGROOT are created with private permissions when missing
    so that the very first run is not blocked by their absence.

    Raises:
        ValueError: If any trusted path fails check_trusted_path.
    """
    check_trusted_path(CONF_PATH, 'autonomy.conf')
    check_trusted_path(CODEX, 'CODEX')
    check_trusted_path(SOCKET.parent, 'SOCKET-katalog')
    ensure_private_dir(PROBE_ROOT, 'PROBE_ROOT')
    ensure_private_dir(LOGROOT, 'LOGROOT')


BASE = Path('/home/web/seaparser/autonomy')
WORK = Path('/home/web/seaparser/worktrees/real-map-data')
POLICY_VERSION = 'capacity-retry-v1'
USAGE_POLICY_VERSION = 'usage-wait-v2'
TIMEOUT_POLICY_VERSION = 'sequential-timeout-v1'
SID = '01a1338f-97d4-7b61-aa78-5fdff31d1f89'
class UnresolvedPin:
    """Sentinel for a mandatory pin that has not been resolved yet.

    Any attempt to use the value (comparison, string conversion, hashing)
    raises ValueError instead of silently behaving like a valid pin.
    """

    def _refuse(self, *_args, **_kwargs):
        raise ValueError('Pin not resolved; call resolve_pins() first')

    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _refuse
    __str__ = __repr__ = __hash__ = __bool__ = _refuse


# Pins are mandatory: an unknown or stale pin must never be treated as valid.
# They are resolved lazily by resolve_pins() so that importing this module
# never depends on the contents of autonomy.conf. Until then they hold an
# UnresolvedPin sentinel that raises on any use.
SSH_HEAD = UnresolvedPin()
HEAD = UnresolvedPin()
MODEL = conf_value("MODEL", "gpt-6.1-sol")
REASONING = conf_value("REASONING", "medium")
# CODEX is the executable spawned for every turn. autonomy.conf is sourced as
# shell code, so this path must be owned by the run user (seaparser) and must
# not be group- or world-writable; otherwise another account could point it at
# an arbitrary binary that runs with our rights. verify_trusted_config()
# enforces this before any launch.
CODEX = conf_value("CODEX", "/home/web/seaparser/.local/bin/codex")
# SOCKET is the tmux socket used to launch workers. It must live in a
# directory owned by the run user and not be group/world-writable, so that no
# other account can pre-create or hijack the socket and inject commands.
# verify_trusted_config() enforces this before any launch.
SOCKET = Path(conf_value("SOCKET", str(BASE / 'tmux.sock')))
SESSION = conf_value("SESSION", "seaparser-autonomy-work")
PROBE_ROOT = Path(conf_value("PROBE_ROOT", str(BASE / 'logs/codex-start-probe')))
# LOGROOT holds the run receipts and status files that gate later passes. It
# must be owned by the run user and not group/world-writable, otherwise
# another account could forge a successful probe/status and unlock work.
# verify_trusted_config() enforces this before any launch.
LOGROOT = Path(conf_value("LOGROOT", str(BASE / 'logs/work-five-hours')))


def resolve_pins():
    """Resolve the mandatory SSH_HEAD/HEAD pins from autonomy.conf.

    Called from preflight()/launch() instead of at import time so that
    importing this module never depends on the contents of autonomy.conf.

    Returns:
        Tuple of (ssh_head, head) as configured strings.

    Raises:
        ValueError: If either pin is missing from autonomy.conf.
    """
    return conf_required('SSH_HEAD'), conf_required('HEAD')


def now():
    return datetime.now(timezone.utc)


def signature(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def user_check():
    if pwd.getpwuid(os.getuid()).pw_name != 'seaparser':
        raise PermissionError('Run as seaparser')


def git(*args):
    return subprocess.check_output(['/usr/bin/git', '-C', str(WORK), *args],
                                   env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'),
                                   stdin=subprocess.DEVNULL, timeout=10).decode().strip()


def proof_workdir_verified(value,source):
    if value == str(WORK):return True
    move=json.loads((BASE/'worktree-relocation.json').read_text())
    return (value==move['old_workdir'] and str(WORK)==move['new_workdir']
            and git('rev-parse','--absolute-git-dir')==move['gitdir']
            and move['proof_hashes'].get(str(source))==hashlib.sha256(source.read_bytes()).hexdigest())


def preflight():
    """Kor forsta sakerhetskontroller och verifiera SSH-bevis.

    Verifies the run user, the trusted config paths, the pinned SSH-disconnect
    receipt, the worktree commit/branch, the preserved handoff hashes and the
    saved ChatGPT login.

    Returns:
        Dict with the initial run metadata recorded in status.json.

    Raises:
        PermissionError: If not running as seaparser.
        ValueError: If any preflight check fails.
    """
    global SSH_HEAD, HEAD
    user_check()
    verify_trusted_config()
    SSH_HEAD, HEAD = resolve_pins()
    proof = json.loads((BASE/'logs/ssh-probe-bd5600c/latest.json').read_text())
    if not (proof['mode'] == 'ssh-disconnect-proof' and proof['exit_code'] == 0
            and proof['ssh_persistence_verified'] is True
            and proof_workdir_verified(proof['workdir'], BASE/'logs/ssh-probe-bd5600c/latest.json')
            and proof['before_disconnect']['commit'] == proof['after_disconnect']['commit'] == SSH_HEAD):
        raise ValueError('Verified pinned SSH handoff is required')
    if git('rev-parse', 'HEAD') != HEAD or git('branch', '--show-current') != 'development/real-map-data':
        raise ValueError('Initial worktree must match the pinned handoff commit/branch')
    if git('status', '--porcelain'):
        handoff = json.loads((BASE/'resume-worktree.json').read_text())
        names = git('ls-files', '--modified', '--others', '--exclude-standard').splitlines()
        actual = {}
        for name in names:
            path = WORK/name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(WORK):
                raise ValueError('Invalid handed-off path: '+name)
            actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if handoff['commit'] != HEAD or actual != handoff['files'] or git('diff','--cached','--name-only'):
            raise ValueError('Local changes differ from explicit preserved handoff; refresh its hashes without resetting files')
    for path in (WORK/'AGENTS.md', BASE/'HANDOFF.md'):
        if not path.is_file() or not path.read_text().strip():
            raise ValueError('Required worktree instructions/handoff unavailable')
    auth = subprocess.run([CODEX, 'login', 'status'], capture_output=True, text=True, timeout=15)
    if auth.returncode or 'Logged in using ChatGPT' not in auth.stdout + auth.stderr:
        raise ValueError('Saved ChatGPT authentication unavailable')
    return {'session_id': None, 'model': MODEL, 'initial_session_mode': 'new',
            'initial_commit': HEAD, 'workdir': str(WORK), 'saved_auth': 'ChatGPT',
            'reasoning_effort': REASONING,
            'original_ssh_transport_proof_workdir': json.loads((BASE/'worktree-relocation.json').read_text())['old_workdir'],
            'usage_retry_policy': USAGE_POLICY_VERSION, 'timeout_policy': TIMEOUT_POLICY_VERSION,
            'preserved_changes_verified': True}


def failure_reason(directory, stem):
    messages = []
    for line in (directory/(stem+'.jsonl')).read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get('type') == 'error':
            messages.append(item.get('message', ''))
        elif item.get('type') == 'turn.failed':
            messages.append(item.get('error', {}).get('message', ''))
    messages.append((directory/(stem+'.stderr.log')).read_text())
    text = '\n'.join(messages).lower()
    if any(x in text for x in ('usage limit', 'usage_limit', 'quota exceeded', 'insufficient_quota', 'usage exhausted', 'usage_exhausted')):
        return 'usage_exhausted'
    if any(x in text for x in ('unauthorized', 'authentication', 'invalid api key', 'token expired', 'not logged in', '401')):
        return 'authentication'
    if 'project_blocker:' in text:
        return 'project_blocker'
    if 'selected model is at capacity' in text:
        return 'model_capacity'
    return 'engine_or_environment'


def usage_reset_at(directory, stem):
    """Only explicit timestamps in engine error events; never tool/command output."""
    found = []
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('resets_at', 'reset_at'):
                    try:
                        if isinstance(item, (int, float)) and not isinstance(item, bool):
                            stamp = float(item)
                        elif isinstance(item, str):
                            parsed = datetime.fromisoformat(item.replace('Z', '+00:00'))
                            if parsed.tzinfo is None:continue
                            stamp = parsed.timestamp()
                        else:continue
                        if time.time() < stamp < time.time()+31*86400:found.append(stamp)
                    except (ValueError, OverflowError):pass
                elif isinstance(item, (dict, list)):collect(item)
        elif isinstance(value, list):
            for item in value:collect(item)
    for line in (directory/(stem+'.jsonl')).read_text().splitlines():
        try:item = json.loads(line)
        except json.JSONDecodeError:continue
        if item.get('type') in ('error', 'turn.failed'):collect(item)
    return max(found) if found else None


def usage_retry(call, deadline, session, event, checkpoint, reset_at,
                stopped=lambda: False, clock=time.monotonic, wall=time.time,
                sleep=time.sleep, capacity=None):
    capacity = capacity or capacity_retry
    attempt = 0
    retry = 0
    last_reason = None
    def invoke(_attempt, remaining):
        nonlocal attempt, last_reason
        result = call(attempt, remaining);attempt += 1;last_reason = result[1]
        return result
    while True:
        if stopped():
            event('RETRY_ABORTED', reason='stop_requested');return 130
        if clock() >= deadline:
            event('USAGE_DEADLINE_REACHED', reason='original_deadline');return 124
        rc = capacity(invoke, deadline, session, event, stopped, clock=clock, sleep=sleep)
        if rc == 0 or last_reason != 'usage_exhausted':return rc
        checkpoint()
        if not session():
            event('USAGE_RETRIES_FAILED', reason='no_session');return rc
        reset = reset_at()
        if reset is not None and reset > wall():
            delay = reset-wall()+60
            source = 'explicit_engine_reset_plus_60_seconds'
        else:
            delay = 1800;source = 'unknown_reset_repeated_30_minute_retry'
        retry += 1
        can_retry = clock()+delay < deadline
        delay = min(delay,max(0,deadline-clock()))
        event('USAGE_RETRY_SCHEDULED' if can_retry else 'USAGE_WAIT_UNTIL_DEADLINE', retry=retry, delay_seconds=delay,
              reset_source=source, session_id=session(), model=MODEL)
        until = min(deadline,clock()+delay)
        while clock() < until:
            if stopped():
                event('RETRY_ABORTED', reason='stop_requested');return 130
            sleep(min(0.5, until-clock()))
        if stopped():
            event('RETRY_ABORTED', reason='stop_requested');return 130
        if not can_retry or clock() >= deadline:
            event('USAGE_DEADLINE_REACHED', reason='original_deadline');return 124
        event('USAGE_RETRY_STARTED', retry=retry, session_id=session(), model=MODEL)


def capacity_retry(call, deadline, session, event, stopped=lambda: False,
                   clock=time.monotonic, sleep=time.sleep):
    for attempt in range(4):
        if stopped() or clock() >= deadline:
            event('RETRY_ABORTED', reason='stop_requested_or_deadline')
            return 124
        rc, reason = call(attempt, max(1, deadline-clock()))
        if rc == 0:
            return 0
        event('TASK_TIMEOUT' if reason=='turn_timeout' else 'MANUAL_STOP' if reason=='manual_stop' else 'ENGINE_FAILURE',
              reason=reason, exit_code=rc, attempt=attempt)
        if reason != 'model_capacity':
            return rc
        if attempt == 3 or not session():
            event('CAPACITY_RETRIES_FAILED', reason='attempts_exhausted' if attempt == 3 else 'no_session_id', exit_code=rc)
            return rc
        delay = (60, 180, 600)[attempt]
        if clock()+delay+15 >= deadline:
            event('CAPACITY_RETRIES_FAILED', reason='original_deadline_insufficient', exit_code=rc)
            return rc
        event('CAPACITY_RETRY_SCHEDULED', retry=attempt+1, delay_seconds=delay,
              session_id=session(), model=MODEL)
        until = clock()+delay
        while clock() < until:
            if stopped():
                event('RETRY_ABORTED', reason='stop_requested')
                return 130
            sleep(min(0.5, until-clock()))
        event('CAPACITY_RETRY_STARTED', retry=attempt+1, session_id=session(), model=MODEL)
    raise AssertionError('unreachable')


def task_budget(remaining, reserve=1200):
    return max(0,min(1800,remaining-reserve-15))


def workspace_signature():
    digest=hashlib.sha256(git('rev-parse','HEAD').encode())
    for name in git('ls-files','--modified','--others','--exclude-standard').splitlines():
        path=WORK/name;digest.update(name.encode())
        if path.is_symlink():digest.update(os.readlink(path).encode())
        elif path.is_file():
            with path.open('rb') as stream:digest.update(hashlib.file_digest(stream,'sha256').digest())
        else:digest.update(b'deleted')
    return digest.hexdigest()


def stop_process(child, event, deadline, clock=time.monotonic, sleep=time.sleep):
    """Reap the leader and stop its process group before any continuation."""
    def alive():
        child.poll()
        try:os.killpg(child.pid,0);return True
        except ProcessLookupError:return False
    for sig,grace in ((signal.SIGINT,5),(signal.SIGTERM,3),(signal.SIGKILL,2)):
        if not alive():break
        event('PROCESS_GROUP_SIGNAL',pid=child.pid,signal=int(sig))
        try:os.killpg(child.pid,sig)
        except ProcessLookupError:break
        until=min(deadline,clock()+grace)
        while alive() and clock()<until:sleep(min(.1,until-clock()))
    if alive():raise RuntimeError('Previous process group did not stop; refuse overlapping continuation')
    rc=child.wait(timeout=1);event('PROCESS_GROUP_STOPPED',pid=child.pid,exit_code=rc)
    return rc


def worker(directory, budget=18000, execute=None, check=preflight, mode='codex-work', resume_state=None):
    directory = Path(directory)
    state = {'mode': mode, 'status': 'preflight', 'session_id': None, 'model': MODEL,
             'workdir': str(WORK), 'codex_process_started': False, 'exit_code': 1,
             'policy_version': POLICY_VERSION, 'usage_retry_policy': USAGE_POLICY_VERSION,
             'timeout_policy': TIMEOUT_POLICY_VERSION}
    stop = False
    child = None
    closing = False
    lock = None
    def request_stop(sig, frame):
        nonlocal stop
        stop = True
        if child is not None and child.poll() is None and not closing:
            try:
                os.killpg(child.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
    for sig in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, request_stop)
    with (directory/'runner.log').open('a', buffering=1) as log:
        def event(name, **data):
            log.write(json.dumps({'at': now().isoformat(), 'event': name, **data})+'\n')
            if name in ('USAGE_RETRY_SCHEDULED','USAGE_WAIT_UNTIL_DEADLINE'):
                state.update(status='usage_wait', usage_wait_seconds=data['delay_seconds'],
                             usage_resume_at=(now()+timedelta(seconds=data['delay_seconds'])).isoformat())
                save()
        def save():
            temporary = directory/'status.json.tmp'
            temporary.write_text(json.dumps(state, indent=2)+'\n')
            temporary.replace(directory/'status.json')
        def checkpoint(kind,stem):
            state.update(ending_commit=git('rev-parse','HEAD'),ending_branch=git('branch','--show-current'),
                         local_changes=git('status','--porcelain'),new_commits=git('log','--format=%H %s',HEAD+'..HEAD'),
                         push_status='unknown; see delivery receipts',ci_status='unknown; see delivery receipts')
            save()
            (directory/f'{kind}-checkpoint-{stem}.json').write_text(json.dumps(state,indent=2)+'\n')
            event(kind.upper()+'_CHECKPOINT_SAVED',stem=stem)
        def once(prompt, seconds, number, attempt=0):
            stem = f'turn-{number:03}' + (f'-retry-{attempt}' if attempt else '')
            nonlocal child
            if execute is not None:
                return execute(prompt, seconds, number)
            args = [CODEX, 'exec', '-C', str(WORK), '--approve-for-me',
                    '--add-dir', str(BASE), '--add-dir', '/home/web/seaparser/data',
                    '-m', MODEL, '-c', f'model_reasoning_effort="{REASONING}"', '--json',
                    '-o', str(directory/f'{stem}.md')]
            if state['session_id']:
                args += ['resume', state['session_id']]
            elif number != 1:
                raise ValueError('No new session ID recorded; refuse to resume preparation session')
            args.append(prompt)
            (directory/f'{stem}.argv.json').write_text(json.dumps(args, indent=2)+'\n')
            with (directory/f'{stem}.jsonl').open('w') as output, (directory/f'{stem}.stderr.log').open('w') as errors:
                child = subprocess.Popen(args, cwd=WORK, stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=errors, start_new_session=True)
                state['codex_process_started'] = True
                state['status'] = 'closing' if closing else 'codex_running'
                save()
                (directory/'started').write_text(now().isoformat()+'\n')
                event('CODEX_PROCESS_STARTED', pid=child.pid, turn=number, closing=closing,
                      task_budget_seconds=seconds, task_end_at=(now()+timedelta(seconds=seconds)).isoformat())
                end = time.monotonic() + max(1, seconds)
                while child.poll() is None and not stop and time.monotonic() < end:
                    time.sleep(0.5)
                timed_out=False
                if child.poll() is None or stop:
                    timed_out=not stop
                    event('TURN_TIMEOUT' if timed_out else 'MANUAL_PROCESS_STOP',turn=number)
                # Even a normally exited leader must not leave live children.
                rc = stop_process(child,event,deadline)
                child = None
                for line in (directory/f'{stem}.jsonl').read_text().splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get('type') == 'thread.started':
                        thread_id = str(uuid.UUID(item['thread_id']))
                        if thread_id == SID or (state['session_id'] and thread_id != state['session_id']):
                            raise ValueError('Unexpected/preparation session identity; refuse continuation')
                        state['session_id'] = thread_id
                        (directory/'session-id.txt').write_text(thread_id+'\n')
                        save()
                        event('SESSION_RECORDED', session_id=thread_id)
                if rc == 0 and not state['session_id']:
                    raise ValueError('Codex exited without a recorded new session ID')
                event('CODEX_PROCESS_EXIT', turn=number, exit_code=rc)
                if stop:return 130,'manual_stop'
                if timed_out:return 124,'turn_timeout'
                return rc
        def turn(prompt, seconds, number):
            # Usage waiting may use the original pass budget, including the
            # closing reserve; keep 15 seconds for process cleanup/final receipt.
            allowed_end = deadline - 15
            last_stem = [None]
            def call(attempt, remaining):
                outcome = once(prompt, min(seconds, remaining), number, attempt)
                stem = f'turn-{number:03}' + (f'-retry-{attempt}' if attempt else '')
                last_stem[0] = stem
                if isinstance(outcome,tuple):rc,reason=outcome
                else:
                    rc=outcome;reason=failure_reason(directory,stem) if rc else None
                state['last_failure_reason'] = reason
                save()
                if rc == 0 and attempt:
                    for suffix in ('.md', '.jsonl'):
                        (directory/(f'turn-{number:03}'+suffix)).write_text((directory/(stem+suffix)).read_text())
                return rc, reason
            return usage_retry(call, allowed_end, lambda: state['session_id'], event,
                               lambda:checkpoint('usage',last_stem[0]), lambda: usage_reset_at(directory,last_stem[0]), lambda: stop)
        (directory/'worker.json').write_text(json.dumps({'pid': os.getpid(), 'start_ticks': signature(os.getpid())})+'\n')
        save()
        try:
            user_check()
            lock = (BASE/'run.lock').open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                state['exit_code'] = 75
                raise ValueError('Another run holds the shared run.lock')
            state.update(check())
            start = now()
            deadline = time.monotonic() + budget
            state.update(started_at=start.isoformat(), end_at=(start+timedelta(seconds=budget)).isoformat(),
                         budget_seconds=budget, worker_pid=os.getpid(), worker_start_ticks=signature(os.getpid()))
            if resume_state is not None:
                state.update(started_at=resume_state['started_at'],end_at=resume_state['end_at'],
                             budget_seconds=resume_state['budget_seconds'])
                original_end=datetime.fromisoformat(resume_state['end_at'])
                if original_end.tzinfo is None or original_end<=now():raise ValueError('Original pass deadline has expired')
                resumed_id=str(uuid.UUID(resume_state['session_id']))
                if resumed_id==SID or resume_state['workdir']!=str(WORK) or resume_state['model']!=MODEL:
                    raise ValueError('Resume worktree/model/session mismatch')
                state.update(session_id=resumed_id,initial_session_mode='resume',resumed_at=start.isoformat(),
                             started_at=resume_state['started_at'],end_at=resume_state['end_at'],
                             budget_seconds=resume_state['budget_seconds'])
                deadline=time.monotonic()+(original_end-now()).total_seconds()
                (directory/'session-id.txt').write_text(resumed_id+'\n')
            (directory/'worker.json').write_text(json.dumps({'pid': os.getpid(), 'start_ticks': signature(os.getpid())})+'\n')
            save()
            event('WORK_START', end_at=state['end_at'], session_id=state['session_id'], workdir=str(WORK))
            if mode == 'git-status-probe':
                prompt = ('Read /home/web/seaparser/worktrees/real-map-data/AGENTS.md and '
                          '/home/web/seaparser/autonomy/HANDOFF.md for context only. '
                          'This turn replaces all earlier development instructions. Only run '
                          'git -C /home/web/seaparser/worktrees/real-map-data status --short --branch '
                          'with GIT_OPTIONAL_LOCKS=0. Do not modify any project file, '
                          'run tests, download, commit or push. Report the actual Git status '
                          'and end your answer with CODEX_GIT_STATUS_PROBE_OK.')
                rc = turn(prompt, budget-15, 1)
                report = directory/'turn-001.md'
                events = [json.loads(line) for line in (directory/'turn-001.jsonl').read_text().splitlines() if line.strip()]
                command_verified = any(e.get('type') == 'item.completed'
                    and e.get('item', {}).get('type') == 'command_execution'
                    and e['item'].get('exit_code') == 0
                    and 'git' in e['item'].get('command', '')
                    and 'status' in e['item'].get('command', '') for e in events)
                answer_verified = any(e.get('type') == 'item.completed'
                    and e.get('item', {}).get('type') == 'agent_message'
                    and 'CODEX_GIT_STATUS_PROBE_OK' in e['item'].get('text', '') for e in events)
                verified = (rc == 0 and state['session_id'] and command_verified and answer_verified
                            and report.exists() and 'CODEX_GIT_STATUS_PROBE_OK' in report.read_text())
                state['git_status_command_verified'] = command_verified
                state['actual_codex_answer_verified'] = verified
                state['exit_code'] = 0 if verified else (rc or 1)
                state['status'] = 'finished' if verified else 'failed'
                return state['exit_code']
            instructions = ('Läs först /home/web/seaparser/worktrees/real-map-data/AGENTS.md och '
                            '/home/web/seaparser/autonomy/HANDOFF.md. Arbetsroten är '
                            '/home/web/seaparser/worktrees/real-map-data.\n') + (BASE/'five-hour-prompt.txt').read_text()
            reserve = min(1200, budget / 4)
            number = 0
            failure = 0
            no_progress=0
            def recover_timeout(number,before):
                nonlocal no_progress,failure
                changed=workspace_signature()!=before
                no_progress=0 if changed else no_progress+1
                state['consecutive_timeouts_without_progress']=no_progress
                checkpoint('timeout',f'turn-{number:03}')
                event('TURN_TIMEOUT_RECOVERY',session_id=state['session_id'],progress=changed,no_progress_timeouts=no_progress)
                if not state['session_id'] or no_progress>=2:
                    state['last_failure_reason']='stalled_no_progress' if state['session_id'] else 'session_unavailable'
                    failure=70;event('TIMEOUT_RECOVERY_STOPPED',reason=state['last_failure_reason'])
                    return False
                return True
            while not stop and time.monotonic() < deadline - reserve:
                number += 1
                remaining = deadline-time.monotonic()
                prompt = instructions + f'\nFaktisk start: {state["started_at"]}. Absolut sluttid: {state["end_at"]}. Nu: {now().isoformat()}. Leverera nästa begränsade del; högst 30 minuter innan sammanfattning.\n'
                seconds=task_budget(remaining,reserve)
                if seconds<1:break
                before=workspace_signature()
                rc = turn(prompt, seconds, number)
                if stop:
                    failure=130
                    break
                if rc and state.get('last_failure_reason')=='turn_timeout':
                    if not recover_timeout(number,before):break
                    continue
                no_progress=0
                if rc:
                    failure = rc
                    event('ENGINE_BLOCKED', exit_code=rc)
                    break
                report = directory/f'turn-{number:03}.md'
                if report.exists() and 'PROJECT_BLOCKER:' in report.read_text():
                    state['project_blocker_report'] = str(report)
                    save()
                    event('PROJECT_BLOCKER', report=str(report), action='continue_independent_backlog')
                if report.exists() and 'BLOCKED_NO_AUTHORIZED_WORK' in report.read_text():
                    event('NO_AUTHORIZED_INDEPENDENT_WORK_REMAINS')
                    break
            closing = True
            state['stop_requested'] = stop
            state['status'] = 'closing'
            save()
            remaining = deadline-time.monotonic()
            if remaining > 30 and not failure:
                number += 1
                final = instructions + '\nAVSLUTSFAS: Starta inga nya uppgifter. Granska egna ändringar, kör relevanta samlade tester, gör selektiva granskade commits och push enbart till development/, kontrollera CI för exakt commit. Bevara annat arbete och osäker CI som uttryckligen overifierad. Skriv slutrapport med arbetsstatus, tester, branch/commits, push/CI-resultat, blockerare och nästa uppgift. Uppdatera HANDOFF.md.\n' + f'Absolut sluttid {state["end_at"]}. Rapport till {directory / "final-report.md"}. Stopp begärt: {stop}.\n'
                while True:
                    before=workspace_signature()
                    remaining=deadline-time.monotonic()
                    if remaining<=15:
                        failure=124;break
                    final_rc = turn(final, min(remaining-15, 1185), number)
                    state['closing_exit_code'] = final_rc
                    if stop:
                        failure=130;break
                    if final_rc and state.get('last_failure_reason')=='turn_timeout':
                        if not recover_timeout(number,before):break
                        number+=1
                        continue
                    report = directory/f'turn-{number:03}.md'
                    if report.exists():
                        (directory/'final-report.md').write_text(report.read_text())
                    failure = final_rc
                    break
            else:
                state['closing_exit_code'] = None if failure else 124
                if failure:
                    event('CLOSING_MODEL_SKIPPED', reason=state.get('last_failure_reason'), exit_code=failure)
                failure = failure or 124
            state['exit_code'] = failure
            state['status'] = 'finished' if not failure else 'finished_with_blocker'
            state['ending_commit'] = git('rev-parse', 'HEAD')
            state['ending_branch'] = git('branch', '--show-current')
            state['local_changes'] = git('status', '--porcelain')
            state['new_commits'] = git('log', '--format=%H %s', HEAD+'..HEAD')
            (directory/'git-status.txt').write_text(git('status', '--short', '--branch')+'\n')
        except Exception as error:
            state['status'] = 'failed'
            state['error_type'] = type(error).__name__
            state['error_message'] = str(error)
            event('FAILED', error_type=type(error).__name__, reason=str(error))
        finally:
            if child is not None:
                try:stop_process(child,event,locals().get('deadline',time.monotonic()+10))
                except Exception as error:
                    state['process_cleanup_error']=str(error)
                    state['exit_code']=70
                    state['status']='failed'
            try:
                state['ending_commit'] = git('rev-parse', 'HEAD')
                state['ending_branch'] = git('branch', '--show-current')
                state['local_changes'] = git('status', '--porcelain')
                state['new_commits'] = git('log', '--format=%H %s', HEAD+'..HEAD')
                (directory/'git-status.txt').write_text(git('status', '--short', '--branch')+'\n')
            except Exception as error:
                state['snapshot_error'] = type(error).__name__
            state['stop_requested']=stop
            state['finished_at'] = now().isoformat()
            state['stop_reason'] = ('manual_stop' if stop else 'original_deadline' if state['exit_code']==124 and time.monotonic()>=locals().get('deadline',float('inf'))-15
                                    else state.get('last_failure_reason') or state.get('error_message') or state['status'])
            # Always produce a model-free receipt, including capacity/usage failures.
            (directory/'runner-summary.json').write_text(json.dumps(state, indent=2)+'\n')
            if not (directory/'final-report.md').exists():
                (directory/'final-report.md').write_text('Modellfritt slutkvitto\n\n' + '\n'.join(
                    f'{key}: {state.get(key)}' for key in ('status', 'exit_code', 'last_failure_reason',
                    'stop_reason', 'started_at', 'end_at', 'finished_at', 'session_id', 'ending_commit',
                    'ending_branch', 'local_changes', 'new_commits')) +
                    '\nPush och CI: se delrapporter; inte verifierade av lokala slutkvittot.\n')
            save()
            event('WORK_END', exit_code=state['exit_code'], status=state['status'])
            if lock is not None:
                lock.close()
    return state['exit_code']


def check_start(probe=False):
    info = preflight()
    if not probe:
        proof = json.loads((PROBE_ROOT/'latest/status.json').read_text())
        if not (proof.get('policy_version') == POLICY_VERSION and proof.get('mode') == 'git-status-probe' and proof.get('exit_code') == 0
                and proof.get('actual_codex_answer_verified') is True
                and proof.get('session_id') and proof.get('session_id') != SID
                and proof.get('initial_session_mode') == 'new'
                and proof.get('git_status_command_verified') is True and proof_workdir_verified(proof.get('workdir'), PROBE_ROOT/'latest/status.json')
                and proof.get('ending_commit') == proof.get('initial_commit')
                and proof.get('ending_branch') == 'development/real-map-data'
                and proof.get('local_changes') == ''):
            raise ValueError('A successful real Codex Git-status probe is required before five-hour work')
        # A project descendant does not invalidate a successful launcher/model proof.
        subprocess.run(['/usr/bin/git', '-C', str(WORK), 'merge-base', '--is-ancestor',
                        proof['ending_commit'], HEAD], check=True, timeout=10,
                       stdin=subprocess.DEVNULL)
        info['codex_probe_verified'] = True
        info['codex_probe_commit'] = proof['ending_commit']
    with (BASE/'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    info['shared_lock_available'] = True
    return info


def check_resume():
    source=(LOGROOT/'latest').resolve(strict=True)
    old=json.loads((source/'runner-summary.json').read_text())
    if old['status'] not in ('finished','finished_with_blocker','failed') or old['mode']!='codex-work':
        raise ValueError('Previous worker must have finished before recovery')
    if (old['workdir']!=str(WORK) or old['model']!=MODEL or old.get('ending_commit')!=git('rev-parse','HEAD')
            or old.get('ending_branch')!=git('branch','--show-current')):
        raise ValueError('Original run and handed-off worktree differ')
    if datetime.fromisoformat(old['end_at'])<=now():raise ValueError('Original pass deadline expired; refuse to extend it')
    sid=str(uuid.UUID(old['session_id']))
    if sid==SID:raise ValueError('Preparation session cannot be resumed')
    if signature(old['worker_pid'])==old['worker_start_ticks']:
        raise ValueError('Previous worker process still exists; refuse overlap')
    info=check_start()
    info.update(session_id=sid,initial_session_mode='resume',original_started_at=old['started_at'],
                original_end_at=old['end_at'],resume_from=str(source),resume_state=old)
    return info


def launch(probe=False,resume=False):
    """Verify trusted config, then start a worker in a detached tmux session.

    Args:
        probe: Start the short git-status probe worker instead of a work pass.
        resume: Resume the previous finished pass instead of starting a new one.

    Returns:
        0 when the Codex process started, 1 when the start was unconfirmed.

    Raises:
        RuntimeError: If tmux fails to start or the worker ends immediately.
        ValueError: If the trusted config or the start checks fail.
    """
    global SSH_HEAD, HEAD
    verify_trusted_config()
    SSH_HEAD, HEAD = resolve_pins()
    info = check_resume() if resume else check_start(probe)
    root = PROBE_ROOT if probe else LOGROOT
    session = SESSION
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (BASE/'work-launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = root/(now().strftime('%Y%m%dT%H%M%SZ')+f'-{os.getpid()}')
        directory.mkdir(mode=0o700)
        (directory/'launch.json').write_text(json.dumps(info, indent=2)+'\n')
        if resume:(directory/'resume-state.json').write_text(json.dumps(info['resume_state'],indent=2)+'\n')
        command = shlex.join(['/usr/bin/python3', '-B', str(BASE/'work_session.py'), '--resume-worker' if resume else '--probe-worker' if probe else '--worker', str(directory)])
        r = subprocess.run(['/usr/bin/tmux', '-S', str(SOCKET), 'new-session', '-d', '-s', session,
                            '-c', str(WORK), command], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        (directory/'launcher.log').write_text(r.stdout+r.stderr)
        if r.returncode:
            raise RuntimeError(f'tmux start failed; inspect {directory / "launcher.log"}')
        temporary = root/'latest.tmp'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(directory, target_is_directory=True)
        temporary.replace(root/'latest')
        for _ in range(40):
            if (directory/'started').exists():
                state = json.loads((directory/'status.json').read_text())
                print(f'CODEX_PROCESS_STARTED SESSION={state.get("session_id") or "new; see session-id.txt after completion"}\nSTART={state["started_at"]}\nEND={state["end_at"]}\nLOGS={directory}\nPuTTY kan stängas manuellt; SSH-sessionen avslutas inte av startaren.', flush=True)
                return 0
            if (directory/'status.json').exists():
                s = json.loads((directory/'status.json').read_text())
                if s['status'] in ('failed', 'finished_with_blocker', 'finished'):
                    raise RuntimeError(f'Worker ended; inspect {directory / "status.json"}')
            time.sleep(0.5)
        print(f'START_UNCONFIRMED; inspect {directory / "status.json"}', flush=True)
        return 1


def stop():
    user_check()
    directory = (LOGROOT/'latest').resolve(strict=True)
    state = json.loads((directory/'status.json').read_text())
    if state['status'] in ('finished', 'finished_with_blocker', 'failed'):
        print('Pass already ended; status:',directory/'status.json')
        return 0
    r = json.loads((directory/'worker.json').read_text())
    if signature(r['pid']) != r['start_ticks']:
        raise ValueError('Worker no longer exists; refuse to signal a reused PID')
    os.kill(r['pid'], signal.SIGUSR1)
    print('Controlled stop requested; closing phase and final logs will follow:',directory)
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if sys.argv[1:] == ['--stop']:
            rc = stop()
        elif sys.argv[1:] == ['--probe']:
            rc = launch(probe=True)
        elif sys.argv[1:] == ['--resume']:
            rc = launch(resume=True)
        elif len(sys.argv)==3 and sys.argv[1]=='--resume-worker':
            rc=worker(sys.argv[2],resume_state=json.loads((Path(sys.argv[2])/'resume-state.json').read_text()))
        elif len(sys.argv) == 3 and sys.argv[1] == '--probe-worker':
            rc = worker(sys.argv[2], budget=180, mode='git-status-probe')
        elif len(sys.argv) == 3 and sys.argv[1] == '--worker':
            rc = worker(sys.argv[2])
        elif sys.argv[1:] == ['--check']:
            print(json.dumps(check_start(), indent=2));rc = 0
        elif sys.argv[1:] == ['--check-resume']:
            info=check_resume();info.pop('resume_state');print(json.dumps(info,indent=2));rc=0
        else:
            rc = launch()
    except Exception as error:
        print('WORK_BLOCKED: '+str(error), file=sys.stderr);rc = 1
    raise SystemExit(rc)
