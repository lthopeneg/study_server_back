import unittest
from unittest.mock import patch

from flask import Flask

from audit import record_audit_event
from extensions import db
from models import SecurityAlert, User
from security_monitor import evaluate_login_event


class SecurityMonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://',SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret-for-security-alerts-32-bytes',MAIL_USERNAME='sender@example.com',
            SIGNUP_APPROVAL_EMAIL='admin@example.com',PUBLIC_FRONTEND_URL='https://example.com')
        db.init_app(self.app); self.context=self.app.app_context(); self.context.push(); db.create_all()
        self.admin=User(login_id='admin',password='hash',email='admin@example.com',role='ADMIN')
        db.session.add(self.admin); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    @patch('security_monitor.mail.send')
    def test_admin_failure_creates_high_alert_and_sends_email_once(self,send_mail):
        with self.app.test_request_context(environ_base={'REMOTE_ADDR':'203.0.113.10'}):
            event=record_audit_event('auth.login',actor=self.admin,outcome='failure')
            evaluate_login_event(event,is_admin=True)
            evaluate_login_event(event,is_admin=True)
        alert=SecurityAlert.query.one()
        self.assertEqual(alert.severity,'high')
        self.assertEqual(alert.occurrence_count,2)
        self.assertIsNotNone(alert.notified_at)
        self.assertEqual(send_mail.call_count,1)

    def test_repeated_user_failures_create_medium_alert(self):
        with self.app.test_request_context(environ_base={'REMOTE_ADDR':'203.0.113.11'}):
            for _ in range(5):
                event=record_audit_event('auth.login',actor_login_id='learner',outcome='failure')
            evaluate_login_event(event,is_admin=False)
        alert=SecurityAlert.query.one()
        self.assertEqual(alert.category,'repeated_login_failure')
        self.assertEqual(alert.severity,'medium')


if __name__=='__main__':
    unittest.main()
