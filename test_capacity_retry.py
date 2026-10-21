"""Offline checks; fake clock and subprocess only, no model calls."""
import runpy
import tempfile
import json
import unittest
from unittest.mock import patch
from pathlib import Path
M = runpy.run_path('/home/web/seaparser/autonomy/work_session.py')

class RetryTests(unittest.TestCase):
    def run_case(self, outcomes, deadline=2000, session='test-session', stop_at=None):
        t = [0.0]; calls=[]; events=[]
        def call(attempt, remaining):
            calls.append((attempt,t[0])); return outcomes[min(attempt,len(outcomes)-1)]
        result=M['capacity_retry'](call,deadline,lambda:session,
            lambda name,**data:events.append((name,data)),
            lambda:stop_at is not None and t[0]>=stop_at,
            clock=lambda:t[0],sleep=lambda seconds:t.__setitem__(0,t[0]+seconds))
        return result,calls,events
    def test_recovers_after_exact_delays(self):
        rc,calls,events=self.run_case([(1,'model_capacity')]*3+[(0,None)])
        self.assertEqual(rc,0);self.assertEqual(calls,[(0,0),(1,60),(2,240),(3,840)])
        self.assertEqual([e[1]['delay_seconds'] for e in events if e[0]=='CAPACITY_RETRY_SCHEDULED'],[60,180,600])
    def test_exhausted(self):
        rc,calls,events=self.run_case([(1,'model_capacity')]);self.assertEqual(rc,1);self.assertEqual(len(calls),4)
        self.assertEqual(events[-1][1]['reason'],'attempts_exhausted')
    def test_other_failures_not_retried(self):
        for reason in ['authentication','usage_exhausted','engine_or_environment','project_blocker']:
            rc,calls,_=self.run_case([(7,reason)]);self.assertEqual(rc,7);self.assertEqual(len(calls),1)
    def test_original_deadline(self):
        rc,calls,events=self.run_case([(1,'model_capacity')],deadline=200)
        self.assertEqual(calls,[(0,0),(1,60)]);self.assertEqual(rc,1)
        self.assertEqual(events[-1][1]['reason'],'original_deadline_insufficient')
    def test_missing_session(self):
        rc,calls,_=self.run_case([(1,'model_capacity')],session=None);self.assertEqual(len(calls),1);self.assertEqual(rc,1)
    def test_stop_interrupts_wait(self):
        rc,calls,_=self.run_case([(1,'model_capacity')],stop_at=10);self.assertEqual(rc,130);self.assertEqual(len(calls),1)
    def test_error_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);(d/'turn.stderr.log').write_text('')
            for message,expected in [('Selected model is at capacity.','model_capacity'),('Usage limit reached','usage_exhausted'),('Authentication failed','authentication'),('Network unavailable','engine_or_environment'),('PROJECT_BLOCKER: source unavailable','project_blocker')]:
                (d/'turn.jsonl').write_text(json.dumps({'type':'turn.failed','error':{'message':message}})+'\n')
                self.assertEqual(M['failure_reason'](d,'turn'),expected)
            (d/'turn.jsonl').write_text(json.dumps({'type':'item.completed','item':{'type':'command_execution','aggregated_output':'Selected model is at capacity.'}})+'\n')
            self.assertEqual(M['failure_reason'](d,'turn'),'engine_or_environment')

class WorkerIntegration(unittest.TestCase):
    def test_same_session_model_and_lock_during_retry(self):
        self.run_failure('Selected model is at capacity.',60)
    def test_usage_worker_checkpoint_same_session_and_lock_during_30_minute_wait(self):
        self.run_failure('Usage limit reached',5400,failures=3)
    def test_worker_manual_stop_during_second_usage_wait(self):
        self.run_failure('Usage limit reached',1900,failures=100,stop_at=1900,expected_rc=130)
    def test_worker_keeps_original_end_time_through_usage_deadline(self):
        self.run_failure('Usage limit reached',1885,failures=100,budget=1900,expected_rc=124)
    def run_failure(self,message,expected_wait,*,failures=1,stop_at=None,budget=18000,expected_rc=0):
        import fcntl,time
        g=M['worker'].__globals__; original_codex=g['CODEX']; original_retry=g['capacity_retry'];original_usage=g['usage_retry'];original_base=g['BASE']
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);fake=d/'fake-codex';g['BASE']=d;(d/'five-hour-prompt.txt').write_text('Test only')
            fake.write_text("""#!/usr/bin/python3
import sys,json
from pathlib import Path
args=sys.argv[1:];sid='12345678-1234-4234-9234-123456789abc'
print(json.dumps({'type':'thread.started','thread_id':sid}))
counter=Path(args[args.index('-o')+1]).parent/'calls.txt'
count=int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(count+1))
if count<FAILURES:
 print(json.dumps({'type':'turn.failed','error':{'message':'Selected model is at capacity.'}}))
 sys.exit(1)
Path(args[args.index('-o')+1]).write_text('BLOCKED_NO_AUTHORIZED_WORK')
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'BLOCKED_NO_AUTHORIZED_WORK'}}))
""".replace('Selected model is at capacity.',message).replace('FAILURES',str(failures)))
            fake.chmod(0o700);g['CODEX']=str(fake);t=[time.monotonic()];origin=t[0];waited=[];ends=[]
            def sleep(seconds):
                with (g['BASE']/'run.lock').open('a') as lock:
                    with self.assertRaises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                waited.append(seconds);t[0]+=seconds
                state=json.loads((d/'status.json').read_text());ends.append(state['end_at'])
                if stop_at is not None and t[0]-origin>=stop_at:
                    import signal
                    signal.getsignal(signal.SIGUSR1)(signal.SIGUSR1,None)
            def retry(*args,**kwargs):
                kwargs.pop('clock',None);kwargs.pop('sleep',None)
                return original_retry(*args,**kwargs,clock=lambda:t[0],sleep=sleep)
            g['capacity_retry']=retry
            g['usage_retry']=lambda *args,**kwargs:original_usage(*args,**kwargs,clock=lambda:t[0],sleep=sleep)
            try:
                with patch.object(g['time'],'monotonic',lambda:t[0]):
                    self.assertEqual(M['worker'](d,budget=budget,check=lambda:{'initial_session_mode':'new'}),expected_rc)
                first=json.loads((d/'turn-001.argv.json').read_text());second=json.loads((d/'turn-001-retry-1.argv.json').read_text())
                self.assertNotIn('resume',first);self.assertEqual(second[second.index('resume')+1],(d/'session-id.txt').read_text().strip())
                for args in (first,second):
                    self.assertEqual(args[args.index('-c')+1], 'model_reasoning_effort="medium"')
                    self.assertIn('/home/web/seaparser/data',args)
                self.assertEqual(first[first.index('-m')+1],second[second.index('-m')+1]);self.assertAlmostEqual(sum(waited),expected_wait)
                state=json.loads((d/'status.json').read_text());self.assertEqual(state['budget_seconds'],budget)
                self.assertEqual(set(ends),{state['end_at']})
                for path in d.glob('turn-001-retry-*.argv.json'):
                    args=json.loads(path.read_text());self.assertEqual(args[args.index('resume')+1],state['session_id'])
                    self.assertEqual(args[args.index('-m')+1],state['model'])
                self.assertTrue((d/'runner-summary.json').exists())
                self.assertTrue((d/'final-report.md').exists())
                if message=='Usage limit reached':self.assertGreaterEqual(len(list(d.glob('usage-checkpoint-*.json'))),2)
                if expected_rc==130:self.assertEqual(state['stop_reason'],'manual_stop')
                if expected_rc==124:self.assertEqual(state['stop_reason'],'original_deadline')
            finally:g['CODEX']=original_codex;g['capacity_retry']=original_retry;g['usage_retry']=original_usage;g['BASE']=original_base

class UsageTests(unittest.TestCase):
    def test_reset_parser_uses_only_explicit_engine_error_times(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);path=d/'turn.jsonl';future=time.time()+3600
            path.write_text(json.dumps({'type':'turn.failed','error':{'resets_at':future}})+'\n')
            self.assertEqual(M['usage_reset_at'](d,'turn'),future)
            path.write_text(json.dumps({'type':'item.completed','resets_at':future})+'\n')
            self.assertIsNone(M['usage_reset_at'](d,'turn'))
            path.write_text(json.dumps({'type':'error','reset_at':'2026-10-18T18:00:00'})+'\n')
            self.assertIsNone(M['usage_reset_at'](d,'turn'))
    def run_case(self, reset=None, deadline=10000, stop_at=None,failures=1,after=(0,None)):
        import fcntl
        t=[0.0];calls=[];events=[];snapshots=[]
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);work=d/'unfinished';work.write_text('preserve me')
            with (d/'run.lock').open('a') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                def sleep(seconds):
                    with (d/'run.lock').open('a') as competing:
                        with self.assertRaises(BlockingIOError):fcntl.flock(competing,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    self.assertEqual(work.read_text(),'preserve me');t[0]+=seconds
                def call(attempt,remaining):
                    calls.append((attempt,t[0]));return (1,'usage_exhausted') if attempt<failures else after
                rc=M['usage_retry'](call,deadline,lambda:'same-session',lambda name,**data:events.append((name,data)),
                    lambda:snapshots.append(t[0]),lambda:reset() if callable(reset) else reset,lambda:stop_at is not None and t[0]>=stop_at,
                    clock=lambda:t[0],wall=lambda:1000+t[0],sleep=sleep)
        return rc,calls,events,snapshots
    def test_unknown_reset_waits_30_minutes_and_resumes_with_lock_and_checkpoint(self):
        rc,calls,events,snapshots=self.run_case();self.assertEqual(rc,0);self.assertEqual(calls,[(0,0),(1,1800)]);self.assertEqual(snapshots,[0])
    def test_known_reset_plus_margin(self):
        rc,calls,_,_=self.run_case(reset=1120);self.assertEqual(rc,0);self.assertEqual(calls,[(0,0),(1,180)])
    def test_repeated_reliable_reset_times_then_success(self):
        resets=iter((1120,1300,1480))
        rc,calls,_,_=self.run_case(reset=lambda:next(resets),failures=3)
        self.assertEqual(rc,0);self.assertEqual(calls,[(0,0),(1,180),(2,360),(3,540)])
    def test_no_deadline_extension(self):
        rc,calls,events,_=self.run_case(deadline=1700);self.assertEqual(rc,124);self.assertEqual(len(calls),1);self.assertEqual(events[-1][1]['reason'],'original_deadline')
    def test_stop_during_wait(self):
        rc,calls,_,_=self.run_case(stop_at=10);self.assertEqual(rc,130);self.assertEqual(len(calls),1)
    def test_repeated_unknown_usage_then_success(self):
        rc,calls,_,snapshots=self.run_case(failures=3)
        self.assertEqual(rc,0);self.assertEqual(calls,[(0,0),(1,1800),(2,3600),(3,5400)]);self.assertEqual(snapshots,[0,1800,3600])
    def test_repeated_unknown_usage_waits_through_entire_five_hour_budget(self):
        rc,calls,events,snapshots=self.run_case(failures=100,deadline=18000)
        self.assertEqual(rc,124);self.assertEqual([t for _,t in calls],list(range(0,18000,1800)))
        self.assertEqual(len(snapshots),10);self.assertEqual(events[-1][0],'USAGE_DEADLINE_REACHED')
    def test_manual_stop_during_second_wait(self):
        rc,calls,_,snapshots=self.run_case(failures=3,stop_at=1900)
        self.assertEqual(rc,130);self.assertEqual(calls,[(0,0),(1,1800)]);self.assertEqual(len(snapshots),2)
    def test_known_reset_beyond_deadline_makes_no_extra_call(self):
        rc,calls,events,_=self.run_case(reset=20000,deadline=1000)
        self.assertEqual(rc,124);self.assertEqual(calls,[(0,0)]);self.assertEqual(events[-1][0],'USAGE_DEADLINE_REACHED')
    def test_permanent_authentication_failure_is_not_usage_retried(self):
        rc,calls,_,_=self.run_case(after=(7,'authentication'))
        self.assertEqual(rc,7);self.assertEqual(calls,[(0,0),(1,1800)])

class TimeoutTests(unittest.TestCase):
    def test_task_budget_fits_remaining_time(self):
        self.assertEqual(M['task_budget'](5000),1800)
        self.assertEqual(M['task_budget'](1800),585)
        self.assertEqual(M['task_budget'](1210),0)

    def test_forced_hang_is_killed_and_reaped_before_next_process(self):
        import subprocess,sys,time,os,signal
        child=subprocess.Popen([sys.executable,'-u','-c',"import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"],start_new_session=True,stdout=subprocess.PIPE,text=True)
        self.addCleanup(lambda:child.kill() if child.poll() is None else None)
        self.assertEqual(child.stdout.readline().strip(),'ready');t=[0.];events=[]
        def sleep(seconds):t[0]+=seconds;time.sleep(.01)
        M['stop_process'](child,lambda name,**data:events.append((name,data)),12,clock=lambda:t[0],sleep=sleep)
        child.stdout.close();self.assertIsNotNone(child.poll())
        with self.assertRaises(ProcessLookupError):os.killpg(child.pid,0)
        self.assertEqual([d['signal'] for n,d in events if n=='PROCESS_GROUP_SIGNAL'],[signal.SIGINT,signal.SIGTERM,signal.SIGKILL])

    def run_worker(self,*,failures=1,progress=True,stop_at=None,budget=120,resume_remaining=None):
        import time,signal,hashlib
        from datetime import datetime,timedelta,timezone
        g=M['worker'].__globals__;original_sleep=time.sleep;real_stop=g['stop_process'];real_usage=g['usage_retry'];t=[0.]
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);fake=d/'fake-codex';fixed=datetime(2026,10,18,20,tzinfo=timezone.utc)
            (d/'five-hour-prompt.txt').write_text('Only fake test commands')
            fake.write_text("""#!/usr/bin/python3
import json,sys,time,signal,os
from pathlib import Path
args=sys.argv[1:];d=Path(args[args.index('-o')+1]).parent
counter=d/'calls';count=int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(count+1));active=d/'active'
if active.exists():
 try:os.kill(int(active.read_text()),0)
 except ProcessLookupError:pass
 else:(d/'OVERLAP').write_text('unsafe');sys.exit(90)
active.write_text(str(os.getpid()))
print(json.dumps({'type':'thread.started','thread_id':'12345678-1234-4234-9234-123456789abc'}),flush=True)
signal.signal(signal.SIGINT,lambda *a:sys.exit(0))
if count<FAILURES:
 if PROGRESS:(d/'progress').write_text(str(count+1))
 (d/'ready').write_text(str(count+1))
 while True:time.sleep(.1)
Path(args[args.index('-o')+1]).write_text('BLOCKED_NO_AUTHORIZED_WORK')
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'BLOCKED_NO_AUTHORIZED_WORK'}}),flush=True)
(d/'ready').write_text(str(count+1))
""".replace('FAILURES',str(failures)).replace('PROGRESS',repr(progress)));fake.chmod(0o700)
            def sleep(seconds):
                # Do not charge virtual time for real OS scheduling/startup.
                wanted=len(list(d.glob('turn-*.argv.json')))
                for _ in range(500):
                    if (d/'ready').exists() and int((d/'ready').read_text() or '0')>=wanted:break
                    original_sleep(.01)
                else:raise AssertionError('Fake Codex failed to become ready')
                t[0]+=seconds;original_sleep(.02)
                import fcntl
                with (d/'run.lock').open('a') as lock:
                    with self.assertRaises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                if stop_at is not None and t[0]>=stop_at:signal.getsignal(signal.SIGUSR1)(signal.SIGUSR1,None)
            def signature():return hashlib.sha256((d/'progress').read_bytes() if (d/'progress').exists() else b'').hexdigest()
            resume=None
            if resume_remaining is not None:
                resume={'session_id':'12345678-1234-4234-9234-123456789abc','model':g['MODEL'],'workdir':str(g['WORK']),
                        'started_at':(fixed-timedelta(hours=4)).isoformat(),'end_at':(fixed+timedelta(seconds=resume_remaining)).isoformat(),'budget_seconds':18000}
            with patch.dict(g,BASE=d,CODEX=str(fake),workspace_signature=signature,
                            now=lambda:fixed+timedelta(seconds=t[0]),
                            usage_retry=lambda *args,**kwargs:real_usage(*args,**kwargs,clock=lambda:t[0],sleep=sleep),
                            task_budget=lambda remaining,reserve:max(0,min(10,remaining-reserve-15)),
                            stop_process=lambda child,event,deadline:real_stop(child,event,deadline,clock=lambda:t[0],sleep=sleep)), \
                 patch.object(time,'monotonic',lambda:t[0]),patch.object(time,'sleep',sleep):
                rc=M['worker'](d,budget=budget,check=lambda:{'initial_session_mode':'new'},resume_state=resume)
            state=json.loads((d/'runner-summary.json').read_text());events=[json.loads(s) for s in (d/'runner.log').read_text().splitlines()]
            args=[json.loads(p.read_text()) for p in sorted(d.glob('turn-*.argv.json'))]
            self.assertFalse((d/'OVERLAP').exists());self.assertTrue((d/'final-report.md').exists())
            for command in args[1:] if resume is None else args:
                self.assertEqual(command[command.index('resume')+1],state['session_id'])
            active=d/'active'
            if active.exists():
                import os
                with self.assertRaises(ProcessLookupError):os.kill(int(active.read_text()),0)
            checkpoints=len(list(d.glob('timeout-checkpoint-*.json')))
            return rc,state,events,args,t[0],resume,checkpoints

    def test_timeout_stops_previous_process_then_resumes_same_session(self):
        rc,state,events,args,_,_,checkpoints=self.run_worker()
        self.assertEqual(rc,0);self.assertGreaterEqual(len(args),2);self.assertEqual(checkpoints,1)
        stopped=next(i for i,e in enumerate(events) if e['event']=='PROCESS_GROUP_STOPPED')
        starts=[i for i,e in enumerate(events) if e['event']=='CODEX_PROCESS_STARTED']
        self.assertLess(stopped,starts[1]);self.assertEqual(state['end_at'],'2026-10-18T20:02:00+00:00')

    def test_own_timeout_is_separate_from_engine_failure(self):
        events=[]
        rc=M['capacity_retry'](lambda *_:(124,'turn_timeout'),100,lambda:'same',
            lambda name,**data:events.append(name),clock=lambda:0)
        self.assertEqual(rc,124);self.assertEqual(events,['TASK_TIMEOUT'])

    def test_expired_pass_refuses_any_model_process_without_extending_end(self):
        rc,state,_,args,_,resume,_=self.run_worker(resume_remaining=-1)
        self.assertEqual(rc,1);self.assertEqual(args,[])
        self.assertEqual(state['end_at'],resume['end_at']);self.assertEqual(state['budget_seconds'],18000)

    def test_repeated_timeouts_without_work_progress_stop(self):
        rc,state,events,args,_,_,checkpoints=self.run_worker(failures=100,progress=False)
        self.assertEqual(rc,70);self.assertEqual(state['stop_reason'],'stalled_no_progress')
        self.assertEqual(len(args),2);self.assertEqual(checkpoints,2)

    def test_manual_stop_does_not_start_another_model(self):
        rc,state,_,args,_,_,_=self.run_worker(failures=100,stop_at=3)
        self.assertEqual(rc,130);self.assertEqual(state['stop_reason'],'manual_stop');self.assertEqual(len(args),1)

    def test_repeated_progressing_timeouts_are_bounded_by_absolute_end(self):
        rc,state,_,_,elapsed,_,_=self.run_worker(failures=100)
        self.assertEqual(rc,124);self.assertLessEqual(elapsed,120);self.assertEqual(state['end_at'],'2026-10-18T20:02:00+00:00')

    def test_resumed_pass_uses_original_session_start_and_deadline(self):
        rc,state,_,args,elapsed,resume,_=self.run_worker(failures=100,resume_remaining=35)
        self.assertEqual(rc,124);self.assertLessEqual(elapsed,35)
        self.assertEqual(state['started_at'],resume['started_at']);self.assertEqual(state['end_at'],resume['end_at'])
        self.assertEqual(state['budget_seconds'],18000);self.assertEqual(state['initial_session_mode'],'resume')

if __name__=='__main__':unittest.main()
