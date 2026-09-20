#!/usr/bin/env python3
"""Reproduce the v1.2.0 preview baseline only, not a general app profiler.

The original recordings used the same ps collection body with a fixed temporary
recording directory. Schema v3 also records Darwin per-process physical
footprint, package-idle and interrupt wake-ups, precise CPU time, and observer
CPU. This copy accepts the directory as an argument, rejects invalid durations
and refuses to replace an existing numeric sample. Executable and process
guards use explicit checks so Python optimization cannot remove them.

Use a freshly extracted, signature-verified copy of the pinned release. Never
point this at an app already in use. It launches synthetic preview state and
terminates only the newly identified process at the exact verified binary path.
"""
from pathlib import Path
import argparse,ctypes,datetime,hashlib,json,os,resource,signal,struct,subprocess,time
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
libproc=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
libproc.proc_pid_rusage.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_void_p]
libproc.proc_pid_rusage.restype=ctypes.c_int
class MachTimebaseInfo(ctypes.Structure):
    _fields_=[('numer',ctypes.c_uint32),('denom',ctypes.c_uint32)]
libsystem=ctypes.CDLL('/usr/lib/libSystem.B.dylib')
libsystem.mach_timebase_info.argtypes=[ctypes.POINTER(MachTimebaseInfo)]
libsystem.mach_timebase_info.restype=ctypes.c_int
timebase=MachTimebaseInfo()
if libsystem.mach_timebase_info(ctypes.byref(timebase)) != 0 or not timebase.denom:
    raise RuntimeError('Could not read the Mach timebase.')
def rusage(pid):
    # Darwin RUSAGE_INFO_V0: UUID then ten uint64 fields. The resource API
    # reports this process's physical footprint and cumulative wake-up counts.
    data=ctypes.create_string_buffer(96)
    if libproc.proc_pid_rusage(pid,0,ctypes.byref(data)):
        raise OSError(ctypes.get_errno(),'proc_pid_rusage failed')
    user,system,package_wakeups,interrupt_wakeups,_,_,resident,physical,_,_=struct.unpack_from('=10Q',data.raw,16)
    return {'resident_bytes':resident,'physical_footprint_bytes':physical,'package_idle_wakeups':package_wakeups,'interrupt_wakeups':interrupt_wakeups,'cpu_time_ticks':user+system}
def observer_cpu_seconds():
    own=resource.getrusage(resource.RUSAGE_SELF)
    children=resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime+own.ru_stime+children.ru_utime+children.ru_stime
flags=['--companion-preview','connected','--companion-scale','1']
flags+=['--companion-folded'] if a.scenario=='folded' else ['--companion-always']
if a.scenario=='hidden':flags+=['--companion-hidden']
def terminate_selected(pid):
    current=rows()
    if pid in current and current[pid]['command']==str(binary):
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:return
        for _ in range(50):
            current=rows()
            if pid not in current or current[pid]['command']!=str(binary):break
            time.sleep(.1)
        else:
            raise RuntimeError('Measurement process did not exit after SIGTERM.')
selected=None
try:
    subprocess.run(['/usr/bin/open','-n','-g',str(app),'--args',*flags],check=True)
    for _ in range(50):
        current=rows()
        added=[pid for pid,v in current.items() if pid not in initial and v['command']==str(binary)]
        if len(added)==1:selected=added[0];break
        if len(added)>1:raise RuntimeError('More than one measurement process appeared.')
        time.sleep(.1)
    if selected is None:raise RuntimeError('Could not identify the newly launched measurement process.')
except BaseException:
    # A launch or discovery failure must not strand a newly created copy.
    if selected is None:
        for _ in range(50):
            current=rows()
            added=[pid for pid,v in current.items() if pid not in initial and v['command']==str(binary)]
            if len(added)==1:selected=added[0];break
            if len(added)>1:break
            time.sleep(.1)
    if selected is not None:terminate_selected(selected)
    raise
try:
    print(json.dumps({'state':'warming','scenario':a.scenario,'warmup_seconds':a.warmup,'measurement_seconds':a.duration}),flush=True)
    time.sleep(a.warmup)
    observer_cpu_start=observer_cpu_seconds()
    start=time.monotonic()
    samples=[]
    observed=set()
    departed=False
    initial_members=None
    topology_changed=False
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
        if initial_members is None:initial_members=frozenset(members)
        elif members != initial_members:topology_changed=True
        elapsed=time.monotonic()-start
        usage=[rusage(pid) for pid in members]
        samples.append({
          'elapsed_seconds':round(elapsed,3),'process_count':len(members),
          'rss_kib_sum':sum(current[pid]['rss_kib'] for pid in members),
          'cpu_seconds_sum':round(sum(current[pid]['cpu_seconds'] for pid in members),4),
          'physical_footprint_bytes_sum':sum(item['physical_footprint_bytes'] for item in usage),
          'package_idle_wakeups_sum':sum(item['package_idle_wakeups'] for item in usage),
          'interrupt_wakeups_sum':sum(item['interrupt_wakeups'] for item in usage),
          'rusage_cpu_time_ticks_sum':sum(item['cpu_time_ticks'] for item in usage),
        })
        if elapsed>=a.duration:break
        time.sleep(min(5,a.duration-elapsed))
    observer_cpu_delta=observer_cpu_seconds()-observer_cpu_start
    measured=samples[-1]['elapsed_seconds']-samples[0]['elapsed_seconds']
    report={
      'schema_id':'hormuz.native-client-idle-sample','schema_version':3,
      'source_commit':'d854a5a453fcbe20cb3f4c1e261e146f2da93855','version':'1.2.0',
      'executable_sha256':'2ece94a031a039d0e27c7ce873787f83f704045e44b0e63cedb71841b9bb3ecc',
      'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'scenario_requested':a.scenario,'preview':'connected synthetic snapshot; saved-session restoration skipped by source guard',
      'conditions':'Developer workstation with other applications active; background LaunchServices launch; UI state not independently observed; no user interactions during sample.',
      'warmup_seconds':a.warmup,'sample_interval_seconds':5,'duration_seconds':measured,'repeat':1,
      'method':'ps process counters plus Darwin proc_pid_rusage RUSAGE_INFO_V0 and mach_timebase_info; root descendants plus processes executing inside this exact app bundle; no arguments or identities retained',
      'samples':samples,'max_process_count':max(s['process_count'] for s in samples),
      'rss_kib_min':min(s['rss_kib_sum'] for s in samples),'rss_kib_max':max(s['rss_kib_sum'] for s in samples),
      'rss_kib_first':samples[0]['rss_kib_sum'],'rss_kib_last':samples[-1]['rss_kib_sum'],
      'cpu_percent_of_one_core':round(100*(samples[-1]['cpu_seconds_sum']-samples[0]['cpu_seconds_sum'])/measured,4) if not topology_changed else None,
      'rusage_cpu_percent_of_one_core':round(100*(samples[-1]['rusage_cpu_time_ticks_sum']-samples[0]['rusage_cpu_time_ticks_sum'])*timebase.numer/timebase.denom/(measured*1e9),6) if not topology_changed else None,
      'rusage_cpu_timebase_numer':timebase.numer,
      'rusage_cpu_timebase_denom':timebase.denom,
      'observer_cpu_seconds':round(observer_cpu_delta,4),
      'physical_footprint_bytes_min':min(s['physical_footprint_bytes_sum'] for s in samples),
      'physical_footprint_bytes_max':max(s['physical_footprint_bytes_sum'] for s in samples),
      'package_idle_wakeups_delta':samples[-1]['package_idle_wakeups_sum']-samples[0]['package_idle_wakeups_sum'] if not topology_changed else None,
      'interrupt_wakeups_delta':samples[-1]['interrupt_wakeups_sum']-samples[0]['interrupt_wakeups_sum'] if not topology_changed else None,
      'process_topology_stable':not topology_changed,
      'child_departures_observed':departed,'limits':['RSS may count shared pages; physical footprint uses Darwin per-process accounting and is summed only over observed members.','Short-lived processes between samples may be missed.','The wake-up fields are Darwin cumulative package-idle and interrupt counters, not an energy measurement.','CPU and wake-up interval deltas are suppressed if observed process membership changes.','Startup latency, GPU, interaction growth, sign-in and active-client behavior are unmeasured.','One recording per directory; numerical regression budgets require wider scenarios and controlled conditions.']
    }
    destination=root/(a.scenario+'-idle-sample.json')
    with destination.open('x') as stream:
        stream.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ['samples','limits','preview','conditions','method']}),flush=True)
finally:
    terminate_selected(selected)
