"""Container supervisor for one web process and one persistent job worker."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time

stopping = False
children: dict[str, subprocess.Popen] = {}


def _command(name: str, default: str) -> list[str]:
    command = shlex.split(os.environ.get(name, default).strip())
    if not command:
        raise RuntimeError(f'{name} cannot be empty.')
    return command


def _start(name: str, command: list[str]) -> subprocess.Popen:
    print(f'[supervisor] starting {name}: {shlex.join(command)}', flush=True)
    process = subprocess.Popen(command, env=os.environ.copy())
    children[name] = process
    return process


def _run_migrations() -> None:
    enabled = os.environ.get('RUN_MIGRATIONS_ON_START', 'true').strip().casefold() in {
        '1', 'true', 'yes', 'on'}
    if not enabled:
        print('[supervisor] automatic migration is disabled', flush=True)
        return
    command = _command(
        'MIGRATION_COMMAND', f'{shlex.quote(sys.executable)} -m flask --app app:app db upgrade')
    print(f'[supervisor] running migration: {shlex.join(command)}', flush=True)
    completed = subprocess.run(command, env=os.environ.copy(), check=False)
    if completed.returncode:
        raise RuntimeError(f'Database migration failed with exit code {completed.returncode}.')


def _stop_children() -> None:
    for name, process in children.items():
        if process.poll() is None:
            print(f'[supervisor] stopping {name}', flush=True)
            process.terminate()
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and any(p.poll() is None for p in children.values()):
        time.sleep(0.25)
    for name, process in children.items():
        if process.poll() is None:
            print(f'[supervisor] killing unresponsive {name}', flush=True)
            process.kill()


def _handle_signal(signum, _frame) -> None:
    global stopping
    print(f'[supervisor] received signal {signum}', flush=True)
    stopping = True


def main() -> int:
    os.environ.setdefault('PYTHONUNBUFFERED', '1')
    web_command = _command(
        'WEB_COMMAND', f'{shlex.quote(sys.executable)} -m gunicorn --config gunicorn.conf.py app:app')
    worker_command = _command(
        'WORKER_COMMAND', f'{shlex.quote(sys.executable)} job_worker.py --poll-seconds 2')
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, _handle_signal)

    _run_migrations()
    web = _start('web', web_command)
    worker = _start('worker', worker_command)
    worker_restarts: list[float] = []
    try:
        while not stopping:
            web_code = web.poll()
            if web_code is not None:
                print(f'[supervisor] web exited with code {web_code}', flush=True)
                return web_code or 1

            worker_code = worker.poll()
            if worker_code is not None:
                now = time.monotonic()
                worker_restarts = [stamp for stamp in worker_restarts if now - stamp < 60]
                if len(worker_restarts) >= 5:
                    print('[supervisor] worker failed 5 times within 60 seconds; exiting', flush=True)
                    return worker_code or 1
                worker_restarts.append(now)
                print(f'[supervisor] worker exited with code {worker_code}; restarting in 2 seconds', flush=True)
                time.sleep(2)
                worker = _start('worker', worker_command)
            time.sleep(0.5)
        return 0
    finally:
        _stop_children()


if __name__ == '__main__':
    raise SystemExit(main())
