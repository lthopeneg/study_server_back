import unittest

from routes.practice import validate_variant


class PracticeValidationTests(unittest.TestCase):
    def test_accepts_line_selection_files_and_answers(self):
        variant, error = validate_variant(
            {
                'problem_type': 'line_selection',
                'files': [
                    {'filename': 'a.py', 'content': 'first\nsecond', 'hint': '힌트'},
                    {'filename': 'b.py', 'content': 'value = input()', 'hint': ''},
                ],
                'answers': [
                    {'filename': 'a.py', 'line': 2},
                    {'filename': 'b.py', 'line': 1},
                ],
            },
            'line_selection',
        )

        self.assertIsNone(error)
        self.assertEqual(len(variant['files']), 2)
        self.assertEqual(variant['answers'][0], {'filename': 'a.py', 'line': 2})

    def test_rejects_blank_answer_without_four_underscores(self):
        variant, error = validate_variant(
            {
                'problem_type': 'secure_blank',
                'files': [{'filename': 'a.py', 'content': 'safe_call()', 'hint': ''}],
                'answers': [{'filename': 'a.py', 'line': 1, 'answer': 'validate()'}],
            },
            'secure_blank',
        )

        self.assertIsNone(variant)
        self.assertIn('언더바 4개', error)

    def test_rejects_path_like_filename(self):
        variant, error = validate_variant(
            {
                'problem_type': 'line_selection',
                'files': [{'filename': '../a.py', 'content': 'value = 1', 'hint': ''}],
                'answers': [{'filename': '../a.py', 'line': 1}],
            },
            'line_selection',
        )

        self.assertIsNone(variant)
        self.assertIn('파일명', error)


if __name__ == '__main__':
    unittest.main()
