#!/usr/bin/python3 -I
"""Authorized idle host service/bootstrap recovery; never unload a DRM module."""
import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys

if __name__=='__main__':
    here=Path(__file__).resolve()
    sys.path.insert(0,str(here.parents[2]/'lib/monitorize-vkms') if here.parent.name=='monitorize-vkms' else str(here.parents[1]))

PROTOCOL=1
RESTART_PROTOCOL=1
GATE=Path('/run/monitorize-vkms/recovery.json')


def read_gate():
    try:fd=os.open(GATE,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    except FileNotFoundError:return None
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode & 0o077 or info.st_size>4096:
            raise ValueError('Unsafe host recovery receipt.')
        value=json.loads(stream.read(4097))
    if not isinstance(value,dict) or type(value.get('uid')) is not int or not isinstance(value.get('token'),str) or len(value['token'])!=32:
        raise ValueError('Invalid host recovery receipt.')
    return value


def check_admission():
    value=read_gate()
    if value and os.environ.get('MONITORIZE_VKMS_RECOVERY_TOKEN')!=value['token']:
        raise RuntimeError('Monitorize host recovery is in progress. Retry after it completes.')


class Recovery:
    def __init__(self,backend):self.backend=backend

    def run(self,uid,*,restart_only=False):
        backend=self.backend
        lock=backend.lock()
        try:
            facts=backend.inspect(uid)
            if facts.get('busy'):return dict(success=False,code='host_in_use',message='The host has an owned display or active capture. Stop it through Monitorize before recovery.')
            if not facts.get('installed_compatible'):return dict(success=False,code='prerequisite_missing',message=facts.get('message','Matching host userspace is required.'))
            if not facts.get('topology_ready') and not facts.get('module_available'):
                return dict(success=False,code='module_missing',message='A compatible module for the running kernel is not installed. A kernel/DKMS installation is required.')
            token=backend.block(uid)
        finally:backend.unlock(lock)
        # The bootstrap acquires the same lifecycle lock itself. Admission remains
        # blocked by the private receipt while the caller releases that lock.
        try:
            if not restart_only and not facts.get('topology_ready'):backend.bootstrap()
            backend.restart_service()
            lock=backend.lock(token)
            try:
                verified=backend.inspect(uid)
                if verified.get('busy'):raise RuntimeError('Host ownership changed during recovery.')
                if not verified.get('topology_ready'):
                    return dict(success=False,code='reboot_required',reboot_required=True,
                        message='The loaded module/topology cannot be initialized safely. Restart the system after installing matching host support.')
                backend.unblock(token)
            finally:backend.unlock(lock)
            return dict(success=True,code='ready',recovery_protocol=PROTOCOL,message='VKMS host service and topology are ready. Settings and pairing retained.')
        except Exception as error:
            # Never claim readiness or remove the admission receipt after failure.
            return dict(success=False,code='host_recovery_pending',partial=True,
                        message='Host recovery needs retry: '+str(error))


class InstalledBackend:
    def __init__(self):
        from monitorize_vkms import helper
        from monitorize_vkms.boot_runtime import require_installed
        require_installed(__file__);require_installed(helper.__file__)
        self.helper=helper

    def run(self,*args):
        result=subprocess.run(list(args),capture_output=True,text=True,check=False,timeout=45)
        if result.returncode:raise RuntimeError((result.stderr or result.stdout or 'Host operation failed.')[-2048:])
        return result.stdout.strip()

    def lock(self,token=None):
        if token:os.environ['MONITORIZE_VKMS_RECOVERY_TOKEN']=token
        else:
            previous=read_gate()
            if previous:
                uid=int(os.environ.get('PKEXEC_UID','0'))
                if previous['uid']!=uid:raise PermissionError('Recovery belongs to another account.')
                os.environ['MONITORIZE_VKMS_RECOVERY_TOKEN']=previous['token']
        return self.helper._acquire_lifecycle_lock()

    def unlock(self,fd):os.close(fd)

    def inspect(self,uid):
        from monitorize_vkms.lease import read_lease, named_slots
        from monitorize_vkms.protocols import installed_capabilities
        self.helper._active_session(uid)
        leases=[read_lease(self.helper.STATE_DIRECTORY,slot) for slot in ('mon1','mon2',*named_slots(self.helper.STATE_DIRECTORY))]
        busy=any(lease is not None for lease in leases)
        # Check directly enabled connectors too: do not trust lease absence alone.
        for connector in Path('/sys/class/drm').glob('card*-*'):
            try:
                if '/faux/monitorize' in str((connector/'device').resolve()) and (connector/'status').read_text().strip()=='connected':busy=True
            except OSError:pass
        active=self.run('/usr/bin/systemctl','list-units','--all','--no-legend','--plain','--no-pager',
                        '--state=active,activating,deactivating','monitorize-remote-login*.service')
        if active.strip():busy=True
        try:
            root=self.helper._configfs_root([]);self.helper._require_bootstrap(root,[]);ready=True
        except Exception:ready=False
        available=ready
        if not ready:
            result=subprocess.run(['/usr/sbin/modinfo','-F','vermagic','monitorize_vkms'],capture_output=True,text=True,timeout=5)
            available=result.returncode==0 and result.stdout.split()[:1]==[os.uname().release]
        library=Path('/usr/lib/monitorize-vkms/monitorize_vkms/helper.py')
        executable=Path('/usr/libexec/monitorize-vkms/monitorize-vkms-helper')
        compatible=(bool(installed_capabilities()) and library.read_bytes()==executable.read_bytes()
            and Path('/usr/lib/monitorize-vkms/monitorize_vkms/recovery.py').read_bytes()==Path(__file__).read_bytes())
        return dict(busy=busy,installed_compatible=compatible,topology_ready=ready,module_available=available,
                    message='Host code/metadata is incomplete or mixed; matching installed userspace is required.')

    def block(self,uid):
        previous=read_gate()
        token=previous['token'] if previous else secrets.token_hex(16)
        if previous and previous['uid']!=uid:raise PermissionError('Recovery belongs to another account.')
        GATE.parent.mkdir(mode=0o700,exist_ok=True)
        info=GATE.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o077:raise ValueError('Unsafe host state directory.')
        temporary=GATE.with_name('.recovery-'+secrets.token_hex(8))
        fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump(dict(uid=uid,token=token,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()),stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,GATE)
        os.environ['MONITORIZE_VKMS_RECOVERY_TOKEN']=token
        return token

    def bootstrap(self):self.run('/usr/bin/systemctl','restart','monitorize-vkms-bootstrap.service')
    def restart_service(self):self.run('/usr/bin/systemctl','restart','monitorize-vkms.service')
    def unblock(self,token):
        value=read_gate()
        if value and value['token']!=token:raise PermissionError('Recovery receipt ownership changed.')
        GATE.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('diagnose','recover','restart'))
    args=parser.parse_args()
    try:
        raw=os.environ.get('PKEXEC_UID','')
        if os.geteuid()!=0 or not raw.isdecimal() or int(raw)<=0:raise ValueError('Authorize host recovery through Polkit.')
        backend=InstalledBackend()
        if args.action in ('recover','restart'):result=Recovery(backend).run(int(raw),restart_only=args.action=='restart')
        else:
            lock=backend.lock()
            try:result=dict(success=True,recovery_protocol=PROTOCOL,facts=backend.inspect(int(raw)))
            finally:backend.unlock(lock)
        print(json.dumps(result));return 0 if result['success'] else 1
    except Exception as error:
        print(json.dumps(dict(success=False,code='host_recovery_failed',message=str(error))));return 1


if __name__=='__main__':raise SystemExit(main())
