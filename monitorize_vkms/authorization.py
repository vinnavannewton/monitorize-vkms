#!/usr/bin/python3 -I
"""Root-owned, account-bound grants for silent fixed VKMS display creation."""
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile

if __name__ == '__main__':
    script=Path(__file__).resolve()
    sys.path.insert(0,str(script.parent.parent if script.parent.name=='monitorize_vkms' else script.parents[2]/'lib/monitorize-vkms'))

DIRECTORY=Path('/var/lib/monitorize-vkms/authorizations')


def _directory(path, *, owner_uid=0):
    path=Path(path)
    for parent in (path,*path.parents):
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022:
            raise PermissionError('Insecure VKMS authorization directory.')
    return path


def _account(uid):
    if type(uid) is not int or uid<=0:raise ValueError('A non-root local account is required.')
    account=pwd.getpwuid(uid)
    return dict(version=1,uid=uid,gid=account.pw_gid,username=account.pw_name,home=account.pw_dir)


def get_state(uid, *, directory=DIRECTORY, owner_uid=0):
    expected=_account(uid)
    result=dict(success=True,authorization_version=1,unattended_enabled=False,repair_required=False)
    try:
        _directory(directory,owner_uid=owner_uid)
        fd=os.open(Path(directory)/f'{uid}.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd) as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode & 0o022 or info.st_nlink!=1 or info.st_size>4096:
                raise PermissionError('Insecure VKMS authorization grant.')
            value=json.load(source)
        if value!=expected:raise ValueError('Authorization no longer matches the local account.')
        result['unattended_enabled']=True
    except FileNotFoundError:
        pass
    except (OSError,ValueError,TypeError):
        result.update(repair_required=True,message='VKMS authorization needs administrator repair. Reauthorize your account.')
    return result


def require_grant(uid):
    if not get_state(uid)['unattended_enabled']:
        raise PermissionError('Enable passwordless VKMS display creation in Monitorize Settings first.')


def configure(uid, enabled, *, directory=DIRECTORY):
    if os.geteuid()!=0:raise PermissionError('Administrator authorization is required.')
    account=_account(uid)
    # Validate existing ancestry before creating directories, never follow a
    # user-controlled path. The only mutable configuration is this account grant.
    directory=Path(directory)
    for parent in reversed((directory,*directory.parents)):
        if not parent.exists():parent.mkdir(mode=0o755)
        _directory(parent)
    target=directory/f'{uid}.json'
    if not enabled:
        target.unlink(missing_ok=True)
    else:
        fd,name=tempfile.mkstemp(prefix='.grant-',dir=directory)
        try:
            with os.fdopen(fd,'w') as output:
                json.dump(account,output);output.flush();os.fsync(output.fileno())
            os.replace(name,target)
        finally:
            Path(name).unlink(missing_ok=True)
    return dict(success=True,message='Passwordless VKMS creation enabled for your account.' if enabled else 'Passwordless VKMS creation disabled for future requests.')


def main():
    try:
        if len(sys.argv)!=2 or sys.argv[1] not in ('enable','disable'):raise ValueError('Use enable or disable.')
        raw=os.environ.get('PKEXEC_UID','')
        if not raw.isdigit() or int(raw)<=0:raise PermissionError('Run authorization through Polkit.')
        from monitorize_vkms.helper import _active_session
        _active_session(int(raw))
        result=configure(int(raw),sys.argv[1]=='enable')
    except Exception as error:
        result=dict(success=False,message=str(error))
    print(json.dumps(result));return 0 if result['success'] else 1


if __name__=='__main__':raise SystemExit(main())
