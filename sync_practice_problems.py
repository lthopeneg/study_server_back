import argparse
import json
import os

from app import app
from services.practice_repository import PracticeRepositoryError, sync_practice_repository


def main():
    parser = argparse.ArgumentParser(description='Git 문제 저장소를 DB와 동기화합니다.')
    parser.add_argument(
        '--root',
        default=os.getenv('PRACTICE_PROBLEMS_PATH', '/data/practice_problems'),
        help='python/ 및 csharp/ 폴더가 들어 있는 문제 저장소 경로',
    )
    parser.add_argument('--admin-login', help='새 문제의 작성자로 기록할 관리자 로그인 ID')
    parser.add_argument('--dry-run', action='store_true', help='검증 후 DB 변경을 취소합니다.')
    args = parser.parse_args()

    try:
        with app.app_context():
            result = sync_practice_repository(args.root, args.admin_login, args.dry_run)
    except PracticeRepositoryError as error:
        parser.exit(1, f'동기화 실패: {error}\n')
    print(json.dumps({'status': 'success', 'dry_run': args.dry_run, **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
