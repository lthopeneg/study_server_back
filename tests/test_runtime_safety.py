import unittest

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

from runtime_safety import database_engine_options, install_request_logging


class RuntimeSafetyTests(unittest.TestCase):
    def test_mysql_options_are_accepted_without_connecting(self):
        uri = 'mysql+pymysql://test:unused@localhost/test'
        engine = create_engine(uri, **database_engine_options(uri))
        self.assertIsInstance(engine.pool, QueuePool)
        self.assertEqual(engine.pool.timeout(), 5)
        self.assertEqual(database_engine_options(uri)['connect_args'], {
            'connect_timeout': 5, 'read_timeout': 15, 'write_timeout': 15,
        })
        engine.dispose()

    def test_sqlite_remains_compatible(self):
        app = Flask(__name__)
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = database_engine_options('sqlite://')
        db = SQLAlchemy(app)
        with app.app_context():
            self.assertEqual(db.session.execute(db.text('SELECT 1')).scalar(), 1)

    def test_logs_pair_requests_without_secrets(self):
        app = Flask(__name__)
        install_request_logging(app)

        @app.get('/example/<name>')
        def example(name):
            return {'ok': True}

        with self.assertLogs('study.requests', level='INFO') as captured:
            response = app.test_client().get('/example/private-name?token=secret-value')
        request_id = response.headers['X-Request-ID']
        self.assertEqual(response.status_code, 200)
        logs = '\n'.join(captured.output)
        self.assertEqual(logs.count(request_id), 2)
        self.assertIn('route=/example/<name>', logs)
        self.assertNotIn('private-name', logs)
        self.assertNotIn('secret-value', logs)

    def test_errors_are_logged_without_exception_message(self):
        app = Flask(__name__)
        app.config['PROPAGATE_EXCEPTIONS'] = True
        install_request_logging(app)

        @app.get('/fail')
        def fail():
            raise RuntimeError('sensitive details')

        with self.assertLogs('study.requests', level='INFO') as captured:
            with self.assertRaises(RuntimeError):
                app.test_client().get('/fail')
        logs = '\n'.join(captured.output)
        self.assertIn('request_failed', logs)
        self.assertNotIn('sensitive details', logs)


if __name__ == '__main__':
    unittest.main()
