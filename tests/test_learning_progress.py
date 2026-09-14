import unittest
from datetime import datetime, timedelta

from flask import Flask

from extensions import db
from models import PracticeProblemAttempt, PracticeProblemSet, User
from routes.user import build_learning_progress


class LearningProgressTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add(User(id=1, login_id='learner', password='hash', email='learner@example.com', role='USER'))
        db.session.add_all([
            PracticeProblemSet(id=10, title='Python 문제', language='Python', major_topic='입력 검증', minor_topic='경계 검사', difficulty='beginner', creation_method='manual', status='published', managed_by='web', created_by=1),
            PracticeProblemSet(id=11, title='C# 문제', language='C#', major_topic='인증', minor_topic='세션 검사', difficulty='intermediate', creation_method='manual', status='published', managed_by='web', created_by=1),
            PracticeProblemSet(id=12, title='비공개 문제', language='Python', major_topic='기타', minor_topic='기타', difficulty='advanced', creation_method='manual', status='draft', managed_by='web', created_by=1),
        ])
        db.session.flush()
        now = datetime(2026, 9, 14, 12, 0, 0)
        db.session.add_all([
            self.make_attempt(1, 10, False, now - timedelta(minutes=5)),
            self.make_attempt(2, 10, True, now),
            self.make_attempt(3, 12, True, now - timedelta(days=1)),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    @staticmethod
    def make_attempt(attempt_id, problem_id, correct, attempted_at):
        return PracticeProblemAttempt(
            id=attempt_id, user_id=1, problem_set_id=problem_id,
            problem_title=f'문제 {problem_id}', language='Python',
            major_topic='입력 검증', minor_topic='경계 검사', difficulty='beginner',
            is_correct=correct, line_selection_correct=correct,
            secure_blank_correct=correct, attempted_at=attempted_at,
        )

    def test_progress_counts_only_currently_published_problems(self):
        result = build_learning_progress(1)
        self.assertEqual(result['summary'], {
            'total_problems': 2, 'attempted_problems': 1, 'completed_problems': 1,
            'completion_rate': 50, 'total_attempts': 3,
        })
        self.assertEqual(result['per_problem']['10']['attempt_count'], 2)
        self.assertTrue(result['per_problem']['10']['completed'])
        self.assertNotIn('12', result['per_problem'])
        self.assertFalse(result['recent_attempts'][2]['problem_available'])

    def test_language_filter_limits_summary_and_history(self):
        result = build_learning_progress(1, 'C#')
        self.assertEqual(result['summary']['total_problems'], 1)
        self.assertEqual(result['summary']['total_attempts'], 0)
        self.assertEqual(result['by_language']['C#']['completed'], 0)
        self.assertEqual(result['recent_attempts'], [])


if __name__ == '__main__':
    unittest.main()
