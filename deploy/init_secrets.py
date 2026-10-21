"""Prepare non-root Compose secrets without printing or replacing their values."""
import base64
import os
import secrets
from pathlib import Path


def prepare(root):
    root=Path(root)
    if root.is_symlink():raise ValueError('Secret directory cannot be a symlink')
    root.mkdir(exist_ok=True,mode=0o700)
    os.chmod(root,0o700)
    for name,value in [('browser-state.key',base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()),('operator.token',secrets.token_urlsafe(32))]:
        path=root/name
        if path.is_symlink():raise ValueError('Secret file cannot be a symlink')
        try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:
            if not path.is_file():raise ValueError('Expected a regular secret file')
        else:
            with os.fdopen(fd,'w') as stream:stream.write(value+'\n')
        # Directory 0700 protects host access. Local Compose secrets are read-only
        # bind mounts, so their inode must be readable by the non-root runtime UID.
        os.chmod(path,0o644)


if __name__=='__main__':
    prepare(Path(__file__).resolve().parent.parent/'private')
    print('Deployment secret files prepared; existing values preserved.')
