"""Container startup: serialized migration, persistent state, separate processes."""
import fcntl
import os
import sys
from pathlib import Path
from installer.configure import prepare


def initialize():
    os.umask(0o077)
    password = os.environ.get('UMBREL_BOOTSTRAP_PASSWORD', '')
    if len(password) < 32:
        raise RuntimeError('Umbrel bootstrap password is missing or too short')
    config = Path(os.environ.get('STREAMINGHOME_CONFIG', '/state/config.json'))
    config.parent.mkdir(parents=True, exist_ok=True)
    with (config.parent / '.initialize.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prepare(config, config.parent, public_url=os.environ.get('PUBLIC_URL', 'http://umbrel.local:8099'))


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('web', 'worker'):
        raise SystemExit('Expected web or worker')
    initialize()
    command = [sys.executable, 'serve.py'] if sys.argv[1] == 'web' else [sys.executable, '-m', 'flask', '--app', 'app', 'worker']
    os.execv(sys.executable, command)
