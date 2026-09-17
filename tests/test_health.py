import unittest
from unittest.mock import patch

from flask import Flask

from routes.health import health_bp


class HealthTests(unittest.TestCase):
    def test_reports_running_revision(self):
        app = Flask(__name__)
        app.register_blueprint(health_bp)
        with patch.dict("os.environ", {"APP_REVISION": "abc123"}):
            response = app.test_client().get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "revision": "abc123"})


if __name__ == "__main__":
    unittest.main()
