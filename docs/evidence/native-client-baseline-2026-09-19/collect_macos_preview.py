#!/usr/bin/env python3
"""Reproduce the v1.2.0 preview baseline only, not a general app profiler.

The original recordings used the same collection body with a fixed temporary
recording directory. This copy accepts that directory as an argument, rejects
invalid durations and refuses to replace an existing numeric sample. Executable
and process guards use explicit checks so Python optimization cannot remove them.

Use a freshly extracted, signature-verified copy of the pinned release. Never
point this at an app already in use. It launches synthetic preview state and
terminates only the newly identified process at the exact verified binary path.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,os,signal,subprocess,time
p=argparse.ArgumentParser()
p.add_argument('scenario',choices=['hidden','visible','folded'])
p.add_argument('--recording-directory',type=Path,required=True,help='Directory containing extracted/Hormuz.app from the pinned v1.2.0 release; numeric reports are written here.')
p.add_argument('--warmup',type=int,default=60)
p.add_argument('--duration',type=int,default=300)
a=p.parse_args()
root=a.recording_directory.resolve()
if a.warmup < 0 or not 1 <= a.duration <= 3600:
    p.error('Warm-up must be nonnegative and duration between 1 and 3600 seconds.')
if (root/(a.scenario+'-idle-sample.json')).exists():
    p.error('Refusing to overwrite an existing sample; use a fresh recording directory.')
app=root/'extracted/Hormuz.app'
binary=app/'Contents/MacOS/Hormuz'
if hashlib.sha256(binary.read_bytes()).hexdigest()!='2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc':
    raise RuntimeError('The executable does not match the pinned released preview.')
def rows():
    raw=subprocess.check_output(['/bin/ps','-axo','pid=,ppid=,rss=,time=,comm='],text=True)
    result={}
    for line in raw.splitlines():
        pieces=line.split(None,4)
        if len(pieces)!=5:continue
        pid,ppid,rss,cpu,command=pieces
        parts=cpu.split(':')
        seconds=float(parts[-1])+60*int(parts[-2])+(3600*int(parts[-3]) if len(parts)>2 else 0)
        result[int(pid)]={'parent':int(ppid),'rss_kib':int(rss),'cpu_seconds':seconds,'command':command}
    return result
initial=rows()
if any(v['command']==str(binary) for v in initial.values()):
    raise RuntimeError('This measurement copy is already running.')
flags=['--companion-preview','connected','--companion-scale','1']
flags+=['--companion-folded'] if a.scenario=='folded' else ['--companion-always']
if a.scenario=='hidden':flags+=['--companion-hidden']
subprocess.run(['/usr/bin/open','-n','-g',str(app),'--args',*flags],check=True)
selected=None
for _ in range(50):
    current=rows()
    added=[pid for pid,v in current.items() if pid not in initial and v['command']==str(binary)]
    if len(added)==1:selected=added[0];break
    time.sleep(.1)
if selected is None:raise RuntimeError('Could not identify the newly launched measurement process.')
print(json.dumps({'state':'warming','scenario':a.scenario,'warmup_seconds':a.warmup,'measurement_seconds':a.duration}),flush=True)
try:
    time.sleep(a.warmup)
    start=time.monotonic()
    samples=[]
    observed=set()
    departed=False
    while True:
        current=rows()
        if selected not in current or current[selected]['command']!=str(binary):raise RuntimeError('Measurement process exited.')
        members={selected}|{pid for pid,v in current.items() if v['command'].startswith(str(app)+'/')}
        while True:
            expanded=members|{pid for pid,v in current.items() if v['parent'] in members}
            if expanded==members:break
            members=expanded
        if observed-members:departed=True
        observed|=members
        elapsed=time.monotonic()-start
        samples.append({'elapsed_seconds':round(elapsed,3),'process_count':len(members),'rss_kib_sum':sum(current[pid]['rss_kib'] for pid in members),'cpu_seconds_sum':round(sum(current[pid]['cpu_seconds'] for pid in members),4)})
        if elapsed>=a.duration:break
        time.sleep(min(5,a.duration-elapsed))
    measured=samples[-1]['elapsed_seconds']-samples[0]['elapsed_seconds']
    report={
      'schema_id':'hormuz.native-client-idle-sample','schema_version':1,
      'source_commit':'d854a5a453fcbe20cb3f4c1e261e146f2da93855','version':'1.2.0',
      'executable_sha256':'2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc',
      'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'scenario_requested':a.scenario,'preview':'connected synthetic snapshot; saved-session restoration skipped by source guard',
      'conditions':'Developer workstation with other applications active; background LaunchServices launch; UI state not independently observed; no user interactions during sample.',
      'warmup_seconds':a.warmup,'sample_interval_seconds':5,'duration_seconds':measured,'repeat':1,
      'method':'ps process counters; root descendants plus processes executing inside this exact app bundle; no arguments or identities retained',
      'samples':samples,'max_process_count':max(s['process_count'] for s in samples),
      'rss_kib_min':min(s['rss_kib_sum'] for s in samples),'rss_kib_max':max(s['rss_kib_sum'] for s in samples),
      'rss_kib_first':samples[0]['rss_kib_sum'],'rss_kib_last':samples[-1]['rss_kib_sum'],
      'cpu_percent_of_one_core':round(100*(samples[-1]['cpu_seconds_sum']-samples[0]['cpu_seconds_sum'])/measured,4) if not departed else None,
      'child_departures_observed':departed,'limits':['RSS is not physical footprint and may count shared pages.','Short-lived processes between samples may be missed.','Startup latency, wake-ups, GPU, interaction growth, sign-in and active-client behavior are unmeasured.','One preliminary run, not the three-run acceptance baseline; no regression budgets.']
    }
    destination=root/(a.scenario+'-idle-sample.json')
    with destination.open('x') as stream:
        stream.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ['samples','limits','preview','conditions','method']}),flush=True)
finally:
    current=rows()
    if selected in current and current[selected]['command']==str(binary):os.kill(selected,signal.SIGTERM)
