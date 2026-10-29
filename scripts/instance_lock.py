"""Process locks released by the OS when a launcher or service exits."""
import errno
import os
from pathlib import Path


class InstanceBusy(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        if os.name == 'nt':
            import msvcrt
            if self.file.seek(0, 2) == 0:
                self.file.write(b'\0')
                self.file.flush()
            self.file.seek(0)
        try:
            if os.name == 'nt':
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise InstanceBusy('工作台进程已在运行或正在启动') from None
            raise
        return self

    def __exit__(self, *_):
        if os.name == 'nt':
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()
