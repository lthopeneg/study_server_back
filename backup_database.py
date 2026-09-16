"""Create and restore encrypted MySQL logical backups."""
import argparse
import gzip
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKUP_DIRECTORY = Path(os.getenv('BACKUP_DIRECTORY', '/backups'))
RETENTION_DAYS = int(os.getenv('BACKUP_RETENTION_DAYS', '14'))


def _required_environment():
    names = ('DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'BACKUP_ENCRYPTION_KEY')
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise RuntimeError('필수 백업 환경변수가 없습니다: ' + ', '.join(missing))
    return {name: os.environ[name] for name in names}


def _client_environment(config):
    environment = os.environ.copy()
    environment['MYSQL_PWD'] = config['DB_PASSWORD']
    environment['BACKUP_PASSPHRASE'] = config['BACKUP_ENCRYPTION_KEY']
    return environment


def _database_command(executable, config):
    return [
        executable, '--host', config['DB_HOST'], '--port', os.getenv('DB_PORT', '3306'),
        '--user', config['DB_USER'], '--default-character-set=utf8mb4', config['DB_NAME'],
    ]


def create_backup():
    config = _required_environment()
    dump_tool = shutil.which('mariadb-dump') or shutil.which('mysqldump')
    if not dump_tool:
        raise RuntimeError('MySQL 백업 도구를 찾을 수 없습니다.')
    BACKUP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = BACKUP_DIRECTORY / f'{config["DB_NAME"]}-{timestamp}.sql.gz.enc'
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=BACKUP_DIRECTORY, suffix='.sql.gz', delete=False) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            with gzip.GzipFile(fileobj=temporary, mode='wb', compresslevel=6) as compressed:
                result = subprocess.run(
                    [*_database_command(dump_tool, config)[:-1], '--single-transaction', '--quick',
                     '--skip-lock-tables', config['DB_NAME']],
                    stdout=compressed, stderr=subprocess.PIPE,
                    env=_client_environment(config), check=False,
                )
        if result.returncode != 0:
            raise RuntimeError('DB 덤프 생성에 실패했습니다: ' + result.stderr.decode('utf-8', 'replace')[-500:])
        subprocess.run([
            'openssl', 'enc', '-aes-256-cbc', '-salt', '-pbkdf2', '-iter', '200000',
            '-in', str(temporary_path), '-out', str(destination), '-pass', 'env:BACKUP_PASSPHRASE',
        ], env=_client_environment(config), check=True, capture_output=True)
        os.chmod(destination, 0o600)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    for backup in BACKUP_DIRECTORY.glob(f'{config["DB_NAME"]}-*.sql.gz.enc'):
        if datetime.fromtimestamp(backup.stat().st_mtime, timezone.utc) < cutoff:
            backup.unlink()
    print(f'Encrypted backup created: {destination.name} ({destination.stat().st_size} bytes)')


def restore_backup(path, confirmation):
    config = _required_environment()
    source = Path(path).resolve()
    if confirmation != config['DB_NAME']:
        raise RuntimeError('--confirm 값이 복구 대상 DB 이름과 일치해야 합니다.')
    if source.parent != BACKUP_DIRECTORY.resolve() or not source.name.endswith('.sql.gz.enc'):
        raise RuntimeError('백업 디렉터리 안의 암호화 백업 파일만 복구할 수 있습니다.')
    mysql_tool = shutil.which('mariadb') or shutil.which('mysql')
    if not mysql_tool:
        raise RuntimeError('MySQL 복구 도구를 찾을 수 없습니다.')
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=BACKUP_DIRECTORY, suffix='.sql.gz', delete=False) as temporary:
            temporary_path = Path(temporary.name)
        os.chmod(temporary_path, 0o600)
        subprocess.run([
            'openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000',
            '-in', str(source), '-out', str(temporary_path), '-pass', 'env:BACKUP_PASSPHRASE',
        ], env=_client_environment(config), check=True, capture_output=True)
        with gzip.open(temporary_path, 'rb') as sql:
            result = subprocess.run(
                _database_command(mysql_tool, config), stdin=sql, stderr=subprocess.PIPE,
                env=_client_environment(config), check=False,
            )
        if result.returncode != 0:
            raise RuntimeError('DB 복구에 실패했습니다: ' + result.stderr.decode('utf-8', 'replace')[-500:])
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
    print(f'Database restored from: {source.name}')


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('create')
    restore = subparsers.add_parser('restore')
    restore.add_argument('path')
    restore.add_argument('--confirm', required=True)
    arguments = parser.parse_args()
    if arguments.command == 'create':
        create_backup()
    else:
        restore_backup(arguments.path, arguments.confirm)


if __name__ == '__main__':
    main()
