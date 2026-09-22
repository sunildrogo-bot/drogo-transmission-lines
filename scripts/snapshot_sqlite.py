"""Create a transactionally consistent, integrity-checked SQLite snapshot."""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path) -> str:
    # sqlite3.Connection's context manager commits or rolls back but does not
    # close the handle. Windows will not replace/delete an open database file.
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as connection:
        result = connection.execute('PRAGMA quick_check').fetchone()[0]
    if result != 'ok':
        raise RuntimeError(f'SQLite integrity check failed: {result}')
    return sha256(path)


def snapshot(source: Path, destination: Path) -> str:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError('Source and destination must be different files.')
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with (
            closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True)) as source_db,
            closing(sqlite3.connect(temporary)) as destination_db,
        ):
            source_db.backup(destination_db)
            destination_db.commit()
        digest = verify(temporary)
        os.replace(temporary, destination)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            path = args.verify.resolve()
            digest = verify(path)
            print(f'Backup: {path}')
        elif args.source and args.destination:
            digest = snapshot(args.source, args.destination)
            print(f'Backup: {args.destination.resolve()}')
        else:
            parser.error('provide --verify FILE or both --source and --destination')
        target = args.verify.resolve() if args.verify else args.destination.resolve()
        print(f'Size: {target.stat().st_size} bytes')
        print('Integrity: ok')
        print(f'SHA256: {digest}')
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f'ERROR: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
