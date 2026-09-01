import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import PracticeProblemSet, User
from schema_migrations import apply_schema_migrations
from services.practice_repository import (
    PracticeRepositoryError,
    load_practice_repository,
    sync_practice_repository,
)


class PracticeRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / 'fixtures' / 'practice_repository'
        self.problem_directory = self.root / 'python' / 'problem_0012'

    def test_loads_and_validates_repository_problem(self):
        problems = load_practice_repository(self.root)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].source_key, 'python/problem_0012')
        self.assertEqual(problems[0].problem_id, 12)
        self.assertEqual(problems[0].payload['language'], 'Python')
        self.assertEqual(problems[0].payload['variants'][0]['files'][0]['filename'], 'app.py')
        self.assertEqual(len(problems[0].source_revision), 64)

    def test_rejects_missing_repository(self):
        with self.assertRaisesRegex(PracticeRepositoryError, '찾을 수 없습니다'):
            load_practice_repository(self.root / 'missing')

    def test_updates_existing_problem_and_skips_unchanged_revision(self):
        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        with app.app_context():
            db.create_all()
            db.session.add(User(
                id=1,
                login_id='admin',
                password='hash',
                email='admin@example.com',
                role='ADMIN',
            ))
            db.session.add(PracticeProblemSet(
                id=12,
                title='이전 제목',
                language='Python',
                major_topic='이전 대주제',
                minor_topic='이전 소주제',
                difficulty='beginner',
                scenario='',
                creation_method='manual',
                status='draft',
                created_by=1,
            ))
            db.session.commit()

            with patch('services.practice_repository.append_problem_variants'):
                first_result = sync_practice_repository(self.root)
            problem = db.session.get(PracticeProblemSet, 12)
            self.assertEqual(first_result['updated'], ['python/problem_0012'])
            self.assertEqual(problem.title, '저장소 동기화 테스트')
            self.assertEqual(problem.status, 'published')
            self.assertEqual(problem.managed_by, 'git')
            self.assertEqual(problem.source_key, 'python/problem_0012')

            with patch('services.practice_repository.append_problem_variants'):
                second_result = sync_practice_repository(self.root)
            self.assertEqual(second_result['skipped'], ['python/problem_0012'])
            self.assertEqual(second_result['created'], [])
            self.assertEqual(second_result['updated'], [])
            db.session.remove()
            db.drop_all()

    def test_schema_migration_is_idempotent_when_unique_constraint_exists(self):
        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        with app.app_context():
            db.create_all()
            apply_schema_migrations()
            apply_schema_migrations()
            db.session.remove()
            db.drop_all()


if __name__ == '__main__':
    unittest.main()
