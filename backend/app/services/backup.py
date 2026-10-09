from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

PROGRAM_DATA = Path(os.environ.get('PROGRAMDATA', Path.home())) / 'CODEZONE'
BACKUP_DIR = PROGRAM_DATA / 'backups'


def _pg_bin(name: str) -> str:
    configured = os.environ.get('CODEZONE_PG_BIN')
    candidates = []
    if configured:
        candidates.append(Path(configured) / name)
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    root = Path(r'C:\Program Files\PostgreSQL')
    if root.exists():
        candidates.extend(root.glob(f'*\\bin\\{name}'))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(f'{name} was not found. PostgreSQL client tools are required for backup/restore.')


def _connection(engine: Engine) -> tuple[str, str, str, int, str]:
    """Return PostgreSQL connection fields without serializing the SQLAlchemy URL.

    SQLAlchemy masks the password when an Engine URL is stringified (for example,
    the string representation of the Engine URL may contain ``***``).  Passing that serialized URL into
    PostgreSQL client tools therefore breaks authentication.  Read the structured
    URL fields directly so the real password can be supplied through PGPASSWORD.
    """
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Backup and restore require a PostgreSQL database.')

    u = engine.url
    user = u.username or ''
    password = u.password or ''
    host = u.host or '127.0.0.1'
    port = u.port or 5432
    database = u.database or ''
    return user, password, host, port, database


def _env(engine: Engine) -> dict[str, str]:
    user, password, host, port, _ = _connection(engine)
    env = os.environ.copy()
    env['PGPASSWORD'] = password
    env['PGUSER'] = user
    env['PGHOST'] = host
    env['PGPORT'] = str(port)
    return env


def backup_database(engine: Engine, reason: str = 'manual') -> Path | None:
    if engine.dialect.name != 'postgresql':
        return None
    with engine.connect() as conn:
        tables = inspect(conn).get_table_names()
    if not tables:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target = BACKUP_DIR / f'CODEZONE-{reason}-{stamp}.dump'
    pg_dump = _pg_bin('pg_dump.exe' if os.name == 'nt' else 'pg_dump')
    _, _, _, _, database = _connection(engine)
    args = [pg_dump, '--format=custom', '--no-owner', '--no-password', '--file', str(target), '--dbname', database]
    result = subprocess.run(args, env=_env(engine), capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError((result.stderr or result.stdout or 'pg_dump failed').strip())
    return target


def restore_database(engine: Engine, backup_file: Path) -> None:
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Restore requires PostgreSQL.')
    backup_file = backup_file.resolve()
    if not backup_file.exists() or backup_file.suffix.lower() != '.dump':
        raise RuntimeError('Select a valid CODEZONE .dump backup file.')
    pg_restore = _pg_bin('pg_restore.exe' if os.name == 'nt' else 'pg_restore')
    _, _, _, _, database = _connection(engine)
    result = subprocess.run(
        [pg_restore, '--clean', '--if-exists', '--no-owner', '--no-password', '--dbname', database, str(backup_file)],
        env=_env(engine), capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or 'pg_restore failed').strip())


def latest_backups(limit: int = 30) -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(BACKUP_DIR.glob('*.dump'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [{'name': p.name, 'path': str(p), 'size': p.stat().st_size, 'modified': datetime.fromtimestamp(p.stat().st_mtime).isoformat()} for p in files]
