import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import PracticeProblemSet, PracticeProblemSyncState, User
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

            problem.title = '홈페이지에서 수정한 제목'
            db.session.commit()
            with patch('services.practice_repository.append_problem_variants'):
                second_result = sync_practice_repository(self.root)
            self.assertEqual(second_result['skipped'], ['python/problem_0012'])
            self.assertEqual(second_result['created'], [])
            self.assertEqual(second_result['updated'], [])
            self.assertEqual(
                db.session.get(PracticeProblemSet, 12).title,
                '홈페이지에서 수정한 제목',
            )
            sync_state = db.session.get(PracticeProblemSyncState, 'python/problem_0012')
            self.assertEqual(sync_state.last_problem_id, 12)
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_does_not_recreate_web_deleted_problem_when_git_is_unchanged(self):
        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        with app.app_context():
            db.create_all()
            repository_problem = load_practice_repository(self.root)[0]
            db.session.add(PracticeProblemSyncState(
                source_key=repository_problem.source_key,
                source_revision=repository_problem.source_revision,
                last_problem_id=12,
            ))
            db.session.commit()

            result = sync_practice_repository(self.root)

            self.assertEqual(result['skipped'], ['python/problem_0012'])
            self.assertIsNone(db.session.get(PracticeProblemSet, 12))
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

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
            db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
