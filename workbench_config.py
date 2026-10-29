"""Portable paths shared by the launcher and device bridge."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def local_config():
    path = ROOT / 'workbench.local.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def migrate_config():
    """One-time host configuration migration; never touches device parameters."""
    import shutil
    import time
    import uuid
    path=ROOT/'workbench.local.json'
    if not path.exists():return
    value=local_config()
    if value.get('schema')==2:return
    backup=ROOT/'.runtime/config-migration';backup.mkdir(parents=True,exist_ok=True)
    shutil.copy2(path,backup/(time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.json'))
    motor=value.setdefault('motor',{})
    motor.pop('node',None)
    motor.setdefault('nativeNode',1)
    value.pop('xigeRoot',None);value['schema']=2
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(path)
