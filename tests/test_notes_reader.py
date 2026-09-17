import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
from models import User
from routes.notes_reader import notes_reader_bp


class NotesReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.directory.name)
        (self.root / "Notes").mkdir()
        (self.root / "Notes" / "01_첫 주차.txt").write_text("# 첫 주차", encoding="utf-8")
        (self.root / "Notes" / "02_둘째 주차.txt").write_text("# 둘째 주차", encoding="utf-8")
        (self.root / "Notes" / "secret.md").write_text("비공개", encoding="utf-8")
        (self.root / "outside.txt").write_text("비공개", encoding="utf-8")
        (self.root / "Reports").mkdir()
        (self.root / "Reports" / "executive_summary.md").write_text("# 연구 요약", encoding="utf-8")

        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY="test-secret-for-notes-reader-32-bytes",
            JWT_TOKEN_LOCATION=["headers"],
        )
        db.init_app(self.app)
        jwt.init_app(self.app)
        self.app.register_blueprint(notes_reader_bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add_all([
            User(id=1, login_id="admin", password="hash", email="admin@example.com", role="ADMIN"),
            User(id=2, login_id="user", password="hash", email="user@example.com", role="USER"),
        ])
        db.session.commit()
        self.client = self.app.test_client()
        self.root_patch = patch.dict("os.environ", {"RESEARCH_NOTES_PATH": str(self.root)})
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.directory.cleanup()

    def headers(self, identity):
        return {"Authorization": f"Bearer {create_access_token(identity=identity)}"}

    def test_lists_weekly_notes_newest_first_and_reads_content(self):
        response = self.client.get("/api/notes", headers=self.headers("admin"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([note["name"] for note in response.json["notes"]], ["02_둘째 주차.txt", "01_첫 주차.txt"])

        response = self.client.get("/api/notes/content", query_string={"name": "02_둘째 주차.txt"}, headers=self.headers("admin"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["content"], "# 둘째 주차")

    def test_requires_administrator_and_authentication(self):
        self.assertEqual(self.client.get("/api/notes").status_code, 401)
        self.assertEqual(self.client.get("/api/notes", headers=self.headers("user")).status_code, 403)
        self.assertEqual(self.client.get("/api/notes/content", query_string={"name": "01_첫 주차.txt"}, headers=self.headers("user")).status_code, 403)

    def test_rejects_path_escape_and_oversized_note(self):
        for name in ("../outside.txt", "secret.md", "Notes/01_첫 주차.txt"):
            response = self.client.get("/api/notes/content", query_string={"name": name}, headers=self.headers("admin"))
            self.assertEqual(response.status_code, 404)

        (self.root / "Notes" / "03_큰 노트.txt").write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
        response = self.client.get("/api/notes", headers=self.headers("admin"))
        self.assertNotIn("03_큰 노트.txt", [note["name"] for note in response.json["notes"]])

    def test_resources_are_admin_only_and_allowlisted(self):
        url = "/api/notes/resources/summary"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.headers("user")).status_code, 403)
        response = self.client.get(url, headers=self.headers("admin"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["content"], "# 연구 요약")
        self.assertEqual(self.client.get("/api/notes/resources/../../outside.txt", headers=self.headers("admin")).status_code, 404)
        self.assertEqual(self.client.get("/api/notes/resources/unknown", headers=self.headers("admin")).status_code, 404)

    def test_rejects_oversized_and_symlinked_resource(self):
        summary = self.root / "Reports" / "executive_summary.md"
        summary.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
        self.assertEqual(self.client.get("/api/notes/resources/summary", headers=self.headers("admin")).status_code, 413)
        summary.unlink()
        try:
            summary.symlink_to(self.root / "outside.txt")
        except OSError:
            self.skipTest("symlinks are unavailable")
        self.assertEqual(self.client.get("/api/notes/resources/summary", headers=self.headers("admin")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
