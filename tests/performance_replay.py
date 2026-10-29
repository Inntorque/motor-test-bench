"""Offline comparison against this task's backup; no device access."""
import importlib.util
import json
from pathlib import Path
import statistics
import tarfile
import tempfile
import time
from workbench.events import EventHub
from workbench.subjects import TestSubjects

root=Path.cwd();backup=Path((root/'.runtime/layout-performance-backup.txt').read_text().strip())
def previous(path,name):
    with tarfile.open(backup/'before.tar.gz') as archive:source=archive.extractfile(path).read()
    target=backup/(name+'.py');target.write_bytes(source)
    spec=importlib.util.spec_from_file_location(name,target);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
old_events=previous('workbench/events.py','events_before');old_subjects=previous('workbench/subjects.py','subjects_before')

def elapsed(function):
    began=time.perf_counter();function();return (time.perf_counter()-began)*1000

def events(cls,filtered=False):
    hub=cls();payload={'motor':{'feedback':{str(i):i*.1 for i in range(70)},'limits':list(range(40))},'frames':[[i,100+i/1000,200,300] for i in range(20)]}
    for i in range(2048):hub.publish('state' if i%10==0 else 'acquisition',payload)
    def run():
        for _ in range(1000):
            gap,batch=hub.after(2040,0,include=('state','sensor_frames','sensor_stream')) if filtered else hub.after(2040,0)
            assert not gap and batch[-1]['id']==2048
    return elapsed(run)

with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'subjects.json'
    path.write_text(json.dumps({'schema':2,'motors':[{'id':str(i),'name':f'Motor {i}'} for i in range(30)],'driverBoards':[],
      'testSetups':[{'id':str(i),'motorId':str(i//2),'name':f'Setup {i}','ratio':1 if i%2 else 9.67,'axis':'motor' if i%2 else 'output'} for i in range(60)],
      'selected':{'motorId':'4','driverBoardId':'','testSetupId':'9'}}))
    old=old_subjects.TestSubjects(path);new=TestSubjects(path);assert old.selection()==new.selection()
    def catalog(instance):
        def run():
            for _ in range(3000):assert instance.selection()['testSetup']['ratio']==1
        return elapsed(run)
    catalog(old);catalog(new);events(old_events.EventHub);events(EventHub,True)
    results={'description':'Isolated Python replay; elapsed function costs, not total service CPU percentages',
             'catalog':{'motors':30,'setups':60,'reads':3000},'events':{'eventHistory':2048,'reads':1000,'eventsPerRead':8}}
    for name,old_run,new_run in [('catalog',lambda:catalog(old),lambda:catalog(new)),('events',lambda:events(old_events.EventHub),lambda:events(EventHub,True))]:
        old_times=[];new_times=[]
        for _ in range(5):old_times.append(old_run());new_times.append(new_run())
        a,b=statistics.median(old_times),statistics.median(new_times)
        results[name].update(beforeMedianMs=a,afterMedianMs=b,reductionPercent=(1-b/a)*100,beforeRunsMs=old_times,afterRunsMs=new_times)
    (root/'.runtime/layout-performance-python.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))
