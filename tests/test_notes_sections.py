import unittest
from unittest.mock import patch

from flask import Flask

import routes.notes as notes_route


class NotesSectionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_lists_only_supported_files_in_requested_section(self):
        with (
            self.app.test_request_context("/?section=notes"),
            patch.object(notes_route, "BASE_NOTES_PATH", r"C:\research_note"),
            patch.object(notes_route, "get_jwt_identity", return_value="admin"),
            patch.object(notes_route, "check_admin_role", return_value=True),
            patch.object(notes_route.os.path, "isdir", return_value=True),
            patch.object(
                notes_route.os,
                "walk",
                return_value=[
                    (r"C:\research_note\Notes", [], ["01_week.txt", "ignore.pdf"])
                ],
            ),
        ):
            response = notes_route.get_section_files.__wrapped__()

            payload = response.get_json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(
                payload["files"],
                [{"name": "01_week.txt", "path": "Notes/01_week.txt"}],
            )

    def test_rejects_unknown_section(self):
        with (
            self.app.test_request_context("/?section=prompts"),
            patch.object(notes_route, "get_jwt_identity", return_value="admin"),
            patch.object(notes_route, "check_admin_role", return_value=True),
        ):
            response, status = notes_route.get_section_files.__wrapped__()

        self.assertEqual(status, 400)
        self.assertEqual(response.get_json()["status"], "error")


if __name__ == "__main__":
    unittest.main()
