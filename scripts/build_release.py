"""Build and verify a source-only, secret-safe application release ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


PACKAGE_ROOT = 'drogo_trans_pilot'
MANIFEST_NAME = 'RELEASE_MANIFEST.json'
EXCLUDED_NAMES = {
    '.env', '.venv', 'venv', 'instance', '.git', '.upgrade_backups',
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.idea', '.vscode', 'node_modules',
}
EXCLUDED_SUFFIXES = {
    '.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.log', '.tmp', '.bak', '.zip', '.pmtiles',
}
PRIVATE_PREFIXES = {
    'static/uploads', 'training_exports', 'backups',
}
PRIVATE_KEY_MARKERS = (
    b'-----BEGIN ' + b'PRIVATE KEY-----',
    b'-----BEGIN RSA ' + b'PRIVATE KEY-----',
    b'-----BEGIN OPENSSH ' + b'PRIVATE KEY-----',
)
SENSITIVE_ASSIGNMENT = re.compile(
    rb'(?im)^[ \t]*(?:SECRET_KEY|DATABASE_URL|GMAIL_APP_PASSWORD|GEMINI_API_KEY|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|STORAGE_ACCESS_KEY|STORAGE_SECRET_KEY)[ \t]*=[ \t]*([^\r\n#]*)$'
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_excluded(relative: str) -> bool:
    # A previously installed release already has its generated manifest.
    # Never treat that generated file as source or the new archive would
    # contain two RELEASE_MANIFEST.json entries.
    if PurePosixPath(relative).name == MANIFEST_NAME:
        return True
    parts = PurePosixPath(relative).parts
    if any(part in EXCLUDED_NAMES for part in parts):
        return True
    lowered = relative.casefold()
    if any(lowered == prefix or lowered.startswith(prefix + '/') for prefix in PRIVATE_PREFIXES):
        return True
    return PurePosixPath(relative).suffix.casefold() in EXCLUDED_SUFFIXES


def secret_reason(relative: str, data: bytes) -> str:
    name = PurePosixPath(relative).name.casefold()
    if name == '.env' or (name.startswith('.env.') and name != '.env.example'):
        return 'environment file'
    if any(marker in data for marker in PRIVATE_KEY_MARKERS):
        return 'private key material'
    # .env.example must contain placeholders, never working secrets.
    if name == '.env.example':
        for match in SENSITIVE_ASSIGNMENT.finditer(data):
            value = match.group(1).strip()
            if value and value not in {b'http://127.0.0.1:5000'}:
                return 'non-empty sensitive value in .env.example'
    return ''


def collect_source(root: Path, require_features: bool = True) -> list[tuple[str, bytes]]:
    if require_features:
        sys.path.insert(0, str(root))
        try:
            from feature_registry import assert_source_compatible
            assert_source_compatible(root)
        finally:
            if sys.path and sys.path[0] == str(root):
                sys.path.pop(0)
    files = []
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        relative = normalized_relative(path, root)
        if is_excluded(relative):
            continue
        data = path.read_bytes()
        reason = secret_reason(relative, data)
        if reason:
            raise RuntimeError(f'Release blocked: {relative} contains {reason}.')
        files.append((relative, data))
    if not any(relative == 'app.py' for relative, _data in files):
        raise RuntimeError('Release blocked: app.py was not found in the source root.')
    return files


def build(source: Path, output: Path, require_features: bool = True) -> None:
    source = source.resolve()
    output = output.resolve()
    if source == output or source in output.parents:
        raise RuntimeError('Output ZIP must be outside the application source directory.')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = collect_source(source, require_features=require_features)
    manifest = {
        'format': 1,
        'package_root': PACKAGE_ROOT,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_only': True,
        'preserve_on_upgrade': ['.env', '.venv', 'instance', 'static/uploads'],
        'files': [{'path': relative, 'size': len(data), 'sha256': sha256_bytes(data)}
                  for relative, data in files],
    }
    temporary = output.with_suffix(output.suffix + '.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for relative, data in files:
                archive.writestr(f'{PACKAGE_ROOT}/{relative}', data)
            archive.writestr(f'{PACKAGE_ROOT}/{MANIFEST_NAME}',
                             json.dumps(manifest, indent=2, sort_keys=True).encode('utf-8'))
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    verify(output)
    print(f'Safe release created: {output}')
    print(f'Files: {len(files)}')


def verify(archive_path: Path) -> None:
    archive_path = archive_path.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        bad_crc = archive.testzip()
        if bad_crc:
            raise RuntimeError(f'Release ZIP CRC failed: {bad_crc}')
        names = archive.namelist()
        manifest_path = f'{PACKAGE_ROOT}/{MANIFEST_NAME}'
        if manifest_path not in names:
            raise RuntimeError('Release manifest is missing.')
        manifest = json.loads(archive.read(manifest_path))
        expected = {item['path']: item for item in manifest.get('files', [])}
        actual = set()
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or '..' in pure.parts:
                raise RuntimeError(f'Unsafe ZIP path: {name}')
            if not name.startswith(PACKAGE_ROOT + '/') or name.endswith('/'):
                continue
            relative = name[len(PACKAGE_ROOT) + 1:]
            if relative == MANIFEST_NAME:
                continue
            if is_excluded(relative):
                raise RuntimeError(f'Private/runtime file included: {relative}')
            data = archive.read(name)
            reason = secret_reason(relative, data)
            if reason:
                raise RuntimeError(f'Release blocked: {relative} contains {reason}.')
            item = expected.get(relative)
            if not item or item.get('size') != len(data) or item.get('sha256') != sha256_bytes(data):
                raise RuntimeError(f'Manifest mismatch: {relative}')
            actual.add(relative)
        if actual != set(expected):
            raise RuntimeError('Release contents do not match the manifest.')
    print(f'Release verified: {archive_path}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--allow-incomplete-source', action='store_true',
                        help='Rollback-only: package an older source without the current feature registry.')
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.verify)
        elif args.output:
            build(args.source, args.output, require_features=not args.allow_incomplete_source)
        else:
            parser.error('provide --output to build or --verify to inspect a release')
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
