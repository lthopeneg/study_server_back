import json
import unittest

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from audit import record_audit_event
from extensions import db
from models import AuditLog, User
from routes.audit import audit_bp


class AuditLogTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False,
                               JWT_SECRET_KEY='audit-test-secret-key-with-32-bytes')
        db.init_app(self.app); JWTManager(self.app); self.app.register_blueprint(audit_bp)
        self.context=self.app.app_context(); self.context.push(); db.create_all()
        self.admin=User(login_id='admin',password='hash',email='admin@example.com',role='ADMIN')
        self.user=User(login_id='user',password='hash',email='user@example.com',role='USER')
        db.session.add_all([self.admin,self.user]); db.session.commit(); self.client=self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def headers(self, identity):
        return {'Authorization':f'Bearer {create_access_token(identity=identity)}'}

    def test_record_removes_sensitive_details_and_hashes_ip(self):
        with self.app.test_request_context(headers={'X-Forwarded-For':'203.0.113.8'}):
            record_audit_event('practice.create', actor=self.admin, target_type='problem', target_id=3,
                               details={'language':'Python','password':'do-not-store','answers':['secret']})
        entry=AuditLog.query.one(); details=json.loads(entry.details_json)
        self.assertEqual(details,{'language':'Python'})
        self.assertNotIn('203.0.113.8',entry.ip_hash)

    def test_only_admin_can_query_and_filters_apply(self):
        with self.app.test_request_context():
            record_audit_event('signup.approve',actor=self.admin,target_type='pending_signup',target_id=1)
        denied=self.client.get('/api/admin/audit-logs',headers=self.headers('user'))
        self.assertEqual(denied.status_code,403)
        response=self.client.get('/api/admin/audit-logs?event_type=signup.approve',headers=self.headers('admin'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['total'],1)
        self.assertEqual(response.get_json()['data'][0]['event_type'],'signup.approve')


if __name__=='__main__':
    unittest.main()
