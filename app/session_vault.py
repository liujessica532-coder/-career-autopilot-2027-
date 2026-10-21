"""Authenticated encryption for browser cookies, storage and form checkpoints."""
import json
import os
from pathlib import Path
from cryptography.fernet import Fernet

class SessionVault:
    def __init__(self, root, key_file):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.cipher = Fernet(Path(key_file).read_bytes().strip())

    def path(self, session_id):
        from uuid import UUID
        return self.root / (str(UUID(session_id)) + '.enc')

    def save(self, session_id, value):
        path = self.path(session_id)
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(self.cipher.encrypt(json.dumps(value).encode()))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)

    def load(self, session_id):
        path = self.path(session_id)
        if not path.exists(): return None
        return json.loads(self.cipher.decrypt(path.read_bytes()))
