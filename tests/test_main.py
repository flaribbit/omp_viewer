import json
import tempfile
import unittest
from pathlib import Path

import main


class SessionParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.previous_sessions_dir = main.SESSIONS_DIR
        self.previous_uploads_dir = main.UPLOADS_DIR
        self.previous_upload_sequence = main.UPLOAD_SEQUENCE
        main.UPLOAD_SEQUENCE = 0
        main.UPLOADS_DIR = Path(self.temporary_directory.name) / "uploads"
        main.SESSIONS_DIR = Path(self.temporary_directory.name)
        self.group = main.SESSIONS_DIR / "project"
        self.group.mkdir()
        self.session = self.group / "example.jsonl"
        records = [
            {"type": "title", "title": "Example session"},
            {"type": "session", "timestamp": "2026-07-20T00:00:00.000Z"},
            {
                "type": "message",
                "timestamp": "2026-07-20T00:01:00.000Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "User **markdown**"}],
                },
            },
            {
                "type": "message",
                "timestamp": "2026-07-20T00:02:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Ignore this."},
                        {"type": "toolCall", "name": "read"},
                        {"type": "text", "text": "Answer $x$"},
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "toolCall", "name": "write"}],
                },
            },
        ]
        self.session.write_text(
            "\n".join(json.dumps(record) for record in records) + "\nnot json\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        main.SESSIONS_DIR = self.previous_sessions_dir
        main.UPLOADS_DIR = self.previous_uploads_dir
        main.UPLOAD_SEQUENCE = self.previous_upload_sequence
        self.temporary_directory.cleanup()

    def test_lists_grouped_sessions_with_title(self) -> None:
        self.assertEqual(
            main.list_sessions(),
            [
                {
                    "name": "project",
                    "sessions": [
                        {
                            "path": "project/example.jsonl",
                            "title": "Example session",
                            "timestamp": "2026-07-20T00:00:00.000Z",
                        }
                    ],
                }
            ],
        )

    def test_reads_only_user_and_assistant_text_content(self) -> None:
        messages = main.read_session("project/example.jsonl")

        self.assertEqual(
            messages,
            [
                {
                    "role": "user",
                    "text": "User **markdown**",
                    "timestamp": "2026-07-20T00:01:00.000Z",
                },
                {
                    "role": "assistant",
                    "text": "Answer $x$",
                    "timestamp": "2026-07-20T00:02:00.000Z",
                },
            ],
        )
        self.assertIsNone(main.read_session("../project/example.jsonl"))

    def test_saves_uploaded_image_to_temp_directory(self) -> None:
        image_data = b"fake png data"

        path = main.save_uploaded_image("image/png; charset=binary", image_data)

        self.assertEqual(path.parent, main.UPLOADS_DIR)
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(path.stem, "00")
        self.assertEqual(path.read_bytes(), image_data)

    def test_upload_sequence_wraps_after_99(self) -> None:
        main.UPLOAD_SEQUENCE = 99

        last_path = main.save_uploaded_image("image/jpeg", b"last")
        wrapped_path = main.save_uploaded_image("image/jpeg", b"wrapped")

        self.assertEqual(last_path.name, "99.jpg")
        self.assertEqual(wrapped_path.name, "00.jpg")
        self.assertEqual(wrapped_path.read_bytes(), b"wrapped")

    def test_rejects_unsupported_upload_type(self) -> None:
        with self.assertRaises(main.UploadError) as context:
            main.save_uploaded_image("text/plain", b"not an image")

        self.assertEqual(context.exception.status, main.HTTPStatus.UNSUPPORTED_MEDIA_TYPE)


if __name__ == "__main__":
    unittest.main()
