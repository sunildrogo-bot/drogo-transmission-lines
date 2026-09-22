"""Audit and optionally repair SQLite foreign-key violations safely.

Dry run (default):
    python scripts/check_data_integrity.py

Backup, repair safe CASCADE/SET NULL violations, then verify:
    python scripts/check_data_integrity.py --repair
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from models import db
from scripts.snapshot_sqlite import snapshot


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _sqlite_path() -> Path:
    if db.engine.url.get_backend_name() != 'sqlite':
        raise RuntimeError('This repair command currently supports SQLite databases only.')
    raw = db.engine.url.database or ''
    if not raw or raw == ':memory:':
        raise RuntimeError('A file-backed SQLite database is required.')
    return Path(raw).resolve()


def _backup_database(source: Path) -> tuple[Path, str]:
    backup_root = source.parent / 'backups'
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y%m%d_%H%M%S')
    destination = backup_root / f'{source.stem}_before_integrity_repair_{stamp}.db'
    digest = snapshot(source, destination)
    return destination, digest


def _foreign_key_specs(connection, table: str) -> dict[int, dict]:
    rows = connection.exec_driver_sql(
        f'PRAGMA foreign_key_list({_quote_identifier(table)})').mappings().all()
    return {int(row['id']): dict(row) for row in rows}


def _violations(connection) -> list[dict]:
    rows = connection.exec_driver_sql('PRAGMA foreign_key_check').all()
    findings = []
    specs_by_table = {}
    for table, rowid, parent, fk_id in rows:
        specs = specs_by_table.setdefault(table, _foreign_key_specs(connection, table))
        spec = specs.get(int(fk_id), {})
        findings.append({
            'table': table,
            'rowid': rowid,
            'parent': parent,
            'fk_id': int(fk_id),
            'column': spec.get('from', ''),
            'on_delete': str(spec.get('on_delete') or 'NO ACTION').upper(),
        })
    return findings


def _print_findings(findings: list[dict]) -> None:
    if not findings:
        print('Foreign-key violations: 0')
        return
    print(f'Foreign-key violations: {len(findings)}')
    for item in findings:
        action = 'set reference to NULL' if item['on_delete'] == 'SET NULL' else (
            'delete orphan child row' if item['on_delete'] == 'CASCADE' else 'manual review required')
        print(
            f"- {item['table']} rowid={item['rowid']} column={item['column']} "
            f"missing_parent={item['parent']} rule={item['on_delete']} action={action}"
        )


def _repair(connection, findings: list[dict]) -> tuple[int, int]:
    repaired = manual = 0
    for item in findings:
        table = _quote_identifier(item['table'])
        if item['on_delete'] == 'SET NULL' and item['column']:
            column = _quote_identifier(item['column'])
            connection.exec_driver_sql(
                f'UPDATE {table} SET {column} = NULL WHERE rowid = ?', (item['rowid'],))
            repaired += 1
        elif item['on_delete'] == 'CASCADE':
            connection.exec_driver_sql(
                f'DELETE FROM {table} WHERE rowid = ?', (item['rowid'],))
            repaired += 1
        else:
            manual += 1
    return repaired, manual


def main() -> int:
    parser = argparse.ArgumentParser(description='Audit SQLite foreign-key integrity.')
    parser.add_argument('--repair', action='store_true',
                        help='Create a verified database backup and repair safe orphan references.')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        source = _sqlite_path()
        print(f'Database: {source}')
        with db.engine.connect() as connection:
            before = _violations(connection)
        _print_findings(before)
        if not args.repair or not before:
            if before and not args.repair:
                print('Dry run only. Re-run with --repair after reviewing these findings.')
            return 0 if not before else 2

        backup, digest = _backup_database(source)
        print(f'Backup: {backup}')
        print(f'Backup SHA256: {digest}')
        with db.engine.begin() as connection:
            repaired, manual = _repair(connection, before)
        with db.engine.connect() as connection:
            after = _violations(connection)
        print(f'Safely repaired: {repaired}')
        print(f'Manual-review findings: {manual}')
        print(f'Remaining violations: {len(after)}')
        if after:
            _print_findings(after)
            return 3
        print('Integrity repair completed successfully.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
