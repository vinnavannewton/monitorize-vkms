"""Boot display reservation primitives; privileged caller holds lifecycle lock.

No implicit adoption, disconnect, CLI overrides or normal-session lease forgery.
A failed connect retains a pending reservation for explicit administrator repair.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time

IDLE_DISPLAY_PROTOCOL = 1
GREETER_ON_DEMAND_PROTOCOL = 1


def boot_output_status(state):
    path=Path(state)/'primary.json'
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o022 or info.st_size>16384:
                raise RuntimeError('Unsafe boot-output owner record.')
            record=json.load(stream)
    except FileNotFoundError:return None
    if not isinstance(record,dict):raise RuntimeError('Invalid boot-output owner record.')
    return record


def _save(state,record):
    state=Path(state);state.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError('Unsafe boot-output state directory.')
    path=state/('.boot.'+secrets.token_hex(8))
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump(record,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(path,state/'primary.json')
        fd=os.open(state,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if path.exists():path.unlink()


def _write_edid(connector,edid):
    (connector/'edid').write_bytes(edid)
    (connector/'edid_enabled').write_text('1')


def ensure_boot_output(*,enabled,connector,state,boot_id,sessions,generate_edid,connect=True):
    if enabled is not True:return {'enabled':False}
    if not boot_id or not isinstance(sessions,list) or any(not isinstance(s,dict) for s in sessions):
        raise RuntimeError('Cannot verify boot context.')
    connector=Path(connector)
    for name in ('status','enabled','dynamic','edid','edid_enabled'):
        p=connector/name
        if p.is_symlink() or not p.is_file():raise RuntimeError('Trusted VKMS topology is unavailable.')
    lease=boot_output_status(state)
    if lease:
        if lease.get('kind')!='boot' or lease.get('boot_id')!=boot_id or lease.get('state') not in ('active','idle'):
            raise RuntimeError('Output is reserved by another or incomplete operation.')
        digest=hashlib.sha256((connector/'edid').read_bytes()).hexdigest()
        if (connector/'status').read_text().strip()!=('2' if lease['state']=='idle' else '1') or digest!=lease.get('edid_sha256'):
            raise RuntimeError('Reserved boot output changed; refusing replacement.')
        return lease
    if any(s.get('class')!='manager' for s in sessions):
        raise RuntimeError('Boot output can only be connected before graphical sessions start.')
    if ((connector/'status').read_text().strip()!='2' or
        (connector/'enabled').read_text().strip()!='1' or
        (connector/'dynamic').read_text().strip()!='1'):
        raise RuntimeError('Boot output must be registered, dynamic and disconnected.')
    edid=generate_edid(1920,1080,60,'mon1')
    if len(edid)!=128 or sum(edid)%256 or edid[:8]!=b'\x00\xff\xff\xff\xff\xff\xff\x00':
        raise RuntimeError('Invalid boot EDID.')
    lease=dict(kind='boot',protected=True,enabled=True,state='pending',phase='greeter',
               boot_id=boot_id,generation=secrets.token_hex(16),slot='mon1',mode=[1920,1080,60],
               edid_sha256=hashlib.sha256(edid).hexdigest())
    _save(state,lease)
    _write_edid(connector,edid)
    (connector/'status').write_text('1' if connect else '2')
    if (connector/'status').read_text().strip()!=('1' if connect else '2'):raise RuntimeError('Boot output status did not apply.')
    lease['state']='active' if connect else 'idle';_save(state,lease)
    return lease


def reserve_boot_output(**values):
    """Boot-time reservation only: no connected screen until client preparation."""
    return ensure_boot_output(**values,connect=False)


def transfer_to_desktop(state,generation,boot_id,uid,session):
    """Authenticated supervisor only: bind reservation, keeping fallback protected.

    Caller independently authenticates logind's active local seat/account/session.
    This does not make the output removable by ordinary session cleanup.
    """
    lease=boot_output_status(state)
    if (not lease or lease.get('kind')!='boot' or lease.get('state')!='active' or
        lease.get('generation')!=generation or lease.get('boot_id')!=boot_id):
        raise RuntimeError('Stale boot-output ownership transfer.')
    if type(uid) is not int or uid<=0 or not isinstance(session,str) or not session:
        raise RuntimeError('Invalid desktop owner.')
    if lease.get('phase')=='desktop' and (lease.get('uid'),lease.get('session'))!=(uid,session):
        raise RuntimeError('Boot output already belongs to another session.')
    lease.update(phase='desktop',uid=uid,session=session)
    _save(state,lease)
    return lease



def adopt_for_session(*,state,generation,boot_id,uid,session,session_resolver,connectors,activate,reactivate=False,timeout=5):
    """Caller holds lifecycle lock; resolver authenticates the live desktop."""
    lease=boot_output_status(state)
    if (not lease or lease.get('kind')!='boot' or lease.get('protected') is not True or
        lease.get('state')!='active' or lease.get('generation')!=generation or lease.get('boot_id')!=boot_id):
        raise RuntimeError('Boot-output reservation changed before adoption.')
    deadline=time.monotonic()+timeout
    while True:
        current=boot_output_status(state)
        if current!=lease:
            raise RuntimeError('Boot-output reservation changed while waiting for DRM readiness.')
        actual,environment=session_resolver(uid)
        if actual!=session:raise RuntimeError('Desktop session changed before adoption.')
        outputs=list(connectors().values())
        candidates=[c for c in outputs if c.get('display')=='mon1' or
                    (lease.get('connector') and c.get('name')==lease['connector'] and c.get('card')==lease.get('card'))]
        if len(candidates)>1:raise RuntimeError('Boot DRM identity is ambiguous.')
        for candidate in candidates:
            if candidate.get('edid_sha256') and candidate['edid_sha256']!=lease.get('edid_sha256'):
                raise RuntimeError('Boot DRM identity changed: EDID mismatch.')
            if lease.get('connector_id') is not None and (
                any(lease.get(k)!=candidate.get(k) for k in ('card','connector_id')) or
                lease.get('connector')!=candidate.get('name')):
                raise RuntimeError('Adopted output identity changed.')
        matches=[c for c in candidates if c.get('status')=='connected' and
                 c.get('display')=='mon1' and c.get('edid_sha256')==lease.get('edid_sha256')]
        if matches:
            output=matches[0]
            break
        if time.monotonic()>=deadline:
            observed=', '.join(str(c.get('name'))+':'+str(c.get('status'))+
                ':edid='+('present' if c.get('edid_sha256') else 'pending') for c in candidates)
            raise RuntimeError('Boot DRM readiness timed out: '+(observed or 'reserved connector not discovered'))
        time.sleep(.1)
    if type(output.get('connector_id')) is not int or output['connector_id']<=0:
        raise RuntimeError('Boot connector has no reliable object identity.')
    if lease.get('phase') in ('desktop','desktop_idle'):
        if (lease.get('uid'),lease.get('session'))!=(uid,session):
            raise RuntimeError('Boot output is already adopted by another session.')
        if lease.get('connector_id') is not None and (any(lease.get(k)!=output.get(k) for k in ('card','connector_id')) or lease.get('connector')!=output.get('name')):
            raise RuntimeError('Adopted output identity changed.')
        if not reactivate:return lease
    # Activate the connected output in the selected compositor; do not disconnect
    # the fallback or claim that normal session allocation created it.
    worker=activate(uid,environment,dict(action='activate',name=output['name'],card=output['card'],
                    connector_id=output['connector_id'],mode=lease['mode'],snapshot=None))
    actual,_=session_resolver(uid)
    if actual!=session:raise RuntimeError('Desktop session ended during adoption; output remains protected.')
    lease.update(phase='desktop',uid=uid,session=session,connector=output['name'],
                 card=output['card'],connector_id=output['connector_id'],desktop=worker.get('desktop'))
    _save(state,lease)
    return lease


def adopt_installed(uid,session):
    """Fixed privileged API for the root-owned remote-login service only."""
    from monitorize_vkms import helper
    from monitorize_vkms.drm import monitorize_drm_connectors
    if os.geteuid()!=0:raise RuntimeError('Boot adoption requires the installed system service.')
    lock=helper._acquire_lifecycle_lock()
    try:
        lease=boot_output_status(helper.STATE_DIRECTORY)
        if not lease:raise RuntimeError('No boot output reservation exists.')
        return adopt_for_session(state=helper.STATE_DIRECTORY,generation=lease.get('generation'),
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),uid=uid,session=session,
            session_resolver=helper._active_session,connectors=monitorize_drm_connectors,activate=helper._run_worker)
    finally:os.close(lock)



def return_to_greeter(*,state,generation,boot_id,sessions,engines_stopped,connector=None):
    """Root supervisor has observed engine/cgroup exit and holds lifecycle lock."""
    if engines_stopped is not True or not isinstance(sessions,list):
        raise RuntimeError('Engines/session state is not safe for greeter return.')
    if any(not isinstance(s,dict) or (s.get('seat')=='seat0' and
           s.get('class') not in ('greeter','manager')) for s in sessions):
        raise RuntimeError('A user session still owns the seat; greeter return refused.')
    lease=boot_output_status(state)
    if (not lease or lease.get('kind')!='boot' or lease.get('state') not in ('active','idle') or
        lease.get('generation')!=generation or lease.get('boot_id')!=boot_id):
        raise RuntimeError('Boot output changed before greeter return.')
    if lease.get('mode')!=[1920,1080,60]:
        raise RuntimeError('Boot mode must be restored safely before greeter capture.')
    if connector is not None:
        _reserved(state,connector,generation,boot_id)
        (Path(connector)/'status').write_text('2')
        if (Path(connector)/'status').read_text().strip()!='2':raise RuntimeError('Greeter reservation did not disconnect.')
        lease['state']='idle'
    lease['phase']='greeter'
    for key in ('uid','session','desktop'):lease.pop(key,None)
    _save(state,lease);return lease


def _reserved(state,connector,generation,boot_id):
    lease=boot_output_status(state)
    if (not lease or lease.get('kind')!='boot' or lease.get('protected') is not True or
        lease.get('state') not in ('active','idle','reconnecting') or
        lease.get('generation')!=generation or lease.get('boot_id')!=boot_id or
        hashlib.sha256((Path(connector)/'edid').read_bytes()).hexdigest()!=lease.get('edid_sha256')):
        raise RuntimeError('Reserved output identity changed.')
    return lease


def idle_for_session(*,state,connector,generation,boot_id,uid,session,session_resolver,engines_stopped):
    if engines_stopped is not True:raise RuntimeError('Capture has not exited; reserved display retained.')
    actual,_=session_resolver(uid)
    if actual!=session:raise RuntimeError('Active desktop session changed.')
    lease=_reserved(state,connector,generation,boot_id)
    if lease.get('uid') is not None and (lease.get('uid'),lease.get('session'))!=(uid,session):
        raise RuntimeError('Reserved output belongs to another desktop.')
    # Deliberate hot-unplug is permitted even for the last/headless output.
    # Its EDID/topology/ownership reservation stay intact for reconnection.
    lease.update(state='idle',phase='desktop_idle',uid=uid,session=session)
    _save(state,lease)
    (Path(connector)/'status').write_text('2')
    if (Path(connector)/'status').read_text().strip()!='2':raise RuntimeError('Reserved output did not disconnect.')
    return lease


def release_for_session(**values):
    """Administrator disablement only; caller proves engines exited and mode off."""
    if values['engines_stopped'] is not True:
        raise RuntimeError('Capture has not exited; reserved display retained.')
    actual,_=values['session_resolver'](values['uid'])
    if actual!=values['session']:raise RuntimeError('Active desktop session changed.')
    lease=_reserved(values['state'],values['connector'],values['generation'],values['boot_id'])
    # Unlike idle/reconnect, retiring a disabled reservation can cross this same
    # account's old login session. Root already proves all engines exited.
    if lease.get('uid') is not None and lease['uid']!=values['uid']:
        raise RuntimeError('Reserved output belongs to another account.')
    (Path(values['connector'])/'status').write_text('2')
    if (Path(values['connector'])/'status').read_text().strip()!='2':
        raise RuntimeError('Reserved output did not disconnect.')
    actual,_=values['session_resolver'](values['uid'])
    if actual!=values['session']:raise RuntimeError('Desktop changed before reservation release.')
    _reserved(values['state'],values['connector'],values['generation'],values['boot_id'])
    path=Path(values['state'])/'primary.json'
    path.unlink()
    fd=os.open(values['state'],os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return dict(success=True,changed=True)


def reconnect_reserved(*,state,connector,generation,boot_id):
    lease=_reserved(state,connector,generation,boot_id)
    status=(Path(connector)/'status').read_text().strip()
    if status not in ('1','2'):raise RuntimeError('Unknown reserved connector status.')
    if status=='2':
        lease['state']='reconnecting';_save(state,lease)
        (Path(connector)/'status').write_text('1')
        if (Path(connector)/'status').read_text().strip()!='1':raise RuntimeError('Reserved output did not reconnect.')
    lease['state']='active'
    _save(state,lease);return lease


def desktop_operation_installed(uid,session,operation,*,engines_stopped=False):
    """Coordinator-only root API; fixed slot, no caller paths or requested modes."""
    from monitorize_vkms import helper
    if os.geteuid()!=0 or operation not in ('prepare','idle','release'):raise RuntimeError('Invalid reserved display operation.')
    lock=helper._acquire_lifecycle_lock()
    try:
        root=helper._configfs_root([]);_,connector=helper._require_bootstrap(root,[],'mon1')
        lease=boot_output_status(helper.STATE_DIRECTORY)
        if not lease:
            if operation=='release':return dict(success=True,changed=False)
            raise RuntimeError('Reserved output lease is missing.')
        generation=lease.get('generation');boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        actual,env=helper._active_session(uid)
        if actual!=session:raise RuntimeError('Active desktop session changed.')
        args=dict(state=helper.STATE_DIRECTORY,connector=connector,generation=generation,boot_id=boot_id)
        if operation=='release':
            return release_for_session(**args,uid=uid,session=session,session_resolver=helper._active_session,
                                       engines_stopped=engines_stopped)
        if operation=='idle':
            return idle_for_session(**args,uid=uid,session=session,session_resolver=helper._active_session,
                                    engines_stopped=engines_stopped)
        lease=_reserved(**args)
        if lease.get('uid') is not None and (lease.get('uid'),lease.get('session'))!=(uid,session):
            raise RuntimeError('Reserved output belongs to another desktop.')
        reconnect_reserved(**args)
        from monitorize_vkms.drm import monitorize_drm_connectors
        return adopt_for_session(state=helper.STATE_DIRECTORY,generation=generation,boot_id=boot_id,
            uid=uid,session=session,session_resolver=helper._active_session,connectors=monitorize_drm_connectors,
            activate=lambda u,e,p:helper._run_worker(u,e,p,timeout=12),reactivate=True)
    finally:os.close(lock)


def _require_root():
    if os.geteuid()!=0:raise RuntimeError('Greeter operations require the installed root coordinator.')


def greeter_operation_installed(operation,*,context_check,activate=None,timeout=12):
    """Root coordinator only. Hotplug first; exact active output before capture."""
    import time
    from monitorize_vkms import helper
    from monitorize_vkms.drm import monitorize_drm_connectors
    _require_root()
    if operation not in ('prepare','idle'):raise RuntimeError('Invalid greeter display operation.')
    lock=helper._acquire_lifecycle_lock()
    try:
        context_check()
        root=helper._configfs_root([]);_,connector=helper._require_bootstrap(root,[],'mon1')
        lease=boot_output_status(helper.STATE_DIRECTORY)
        if not lease:raise RuntimeError('Greeter reservation is missing.')
        args=dict(state=helper.STATE_DIRECTORY,connector=connector,generation=lease.get('generation'),
                  boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        lease=_reserved(**args)
        if lease.get('phase')!='greeter':raise RuntimeError('The greeter no longer owns this output.')
        if operation=='idle':
            context_check()  # Caller additionally proves capture cgroup/identity exit.
            (Path(connector)/'status').write_text('2')
            if (Path(connector)/'status').read_text().strip()!='2':raise RuntimeError('Greeter output did not disconnect.')
            lease['state']='idle';_save(helper.STATE_DIRECTORY,lease);return lease
        reconnect_reserved(**args)
        deadline=time.monotonic()+timeout;fallback_at=time.monotonic()+3;activated=False
        while time.monotonic()<deadline:
            context_check()
            matches=[v for v in monitorize_drm_connectors().values() if v.get('display')=='mon1'
                     and v.get('edid_sha256')==lease['edid_sha256'] and v.get('status')=='connected']
            if len(matches)>1:raise RuntimeError('Ambiguous greeter DRM identity.')
            if matches:
                output=matches[0]
                if type(output.get('connector_id')) is not int or output['connector_id']<=0:
                    raise RuntimeError('Greeter connector object identity is unavailable.')
                if lease.get('connector_id') is not None and (lease['connector_id']!=output['connector_id']
                    or lease.get('card')!=output['card'] or lease.get('connector')!=output['name']):
                    raise RuntimeError('Greeter DRM identity changed.')
                try:enabled=(Path(output['path'])/'enabled').read_text().strip()=='enabled'
                except OSError:enabled=False
                if enabled:
                    context_check()
                    lease.update(connector=output['name'],card=output['card'],connector_id=output['connector_id'])
                    _save(helper.STATE_DIRECTORY,lease);return lease
                if activate is not None and not activated and time.monotonic()>=fallback_at:
                    activated=True;activate(output,max(.1,deadline-time.monotonic()))
            time.sleep(.1)
        raise RuntimeError('Greeter did not activate the virtual output; this compositor needs a supported activation adapter.')
    finally:os.close(lock)


def main():
    """Fixed installed boot entry point, intentionally without command arguments."""
    import subprocess
    import sys
    # Standalone installed script under isolated Python; never import a checkout.
    sys.path.insert(0, '/usr/lib/monitorize-vkms')
    from monitorize_vkms import helper
    from monitorize_vkms.edid import generate_edid
    if os.geteuid()!=0:raise RuntimeError('Boot-output setup requires the installed system service.')
    config=Path('/etc/monitorize/remote-login.json')
    fd=os.open(config,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd) as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022 or info.st_size>4096:
            raise RuntimeError('Unsafe remote-login administrator configuration.')
        settings=json.load(source)
    if not isinstance(settings,dict):raise RuntimeError('Invalid remote-login administrator configuration.')
    if settings.get('enabled') is not True:
        print(json.dumps({'enabled':False}));return 0
    lock=helper._acquire_lifecycle_lock()
    try:
        root=helper._configfs_root([])
        _,connector=helper._require_bootstrap(root,[],'mon1')
        sessions=json.loads(subprocess.run(['/usr/bin/loginctl','list-sessions','--json=short'],
            check=True,capture_output=True,text=True,timeout=5).stdout)
        result=reserve_boot_output(enabled=True,connector=connector,state=helper.STATE_DIRECTORY,
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            sessions=sessions,generate_edid=generate_edid)
        print(json.dumps(result));return 0
    finally:os.close(lock)


if __name__=='__main__':
    raise SystemExit(main())


