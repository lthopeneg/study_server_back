"""Read-only access to weekly research notes for administrators."""

import os
import re
from pathlib import Path

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from models import User

notes_reader_bp = Blueprint("notes_reader", __name__)

DEFAULT_RESEARCH_ROOT = (
    Path(__file__).resolve().parents[2] / "research_note"
    if os.name == "nt"
    else Path("/home/ubuntu/research_note")
)
MAX_NOTE_BYTES = 1024 * 1024
NOTE_NAME_PATTERN = re.compile(r"^\d+_[^/\\]+\.txt$", re.IGNORECASE)


def _notes_directory():
    return Path(os.getenv("RESEARCH_NOTES_PATH", str(DEFAULT_RESEARCH_ROOT))) / "Notes"


def _admin_only():
    return User.query.filter_by(login_id=get_jwt_identity(), role="ADMIN").first() is not None


def _safe_note_path(notes_dir, name):
    if not NOTE_NAME_PATTERN.fullmatch(name):
        return None
    candidate = notes_dir / name
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if candidate.resolve(strict=True).parent != notes_dir.resolve(strict=True):
            return None
        if candidate.stat().st_size > MAX_NOTE_BYTES:
            return None
    except OSError:
        return None
    return candidate


@notes_reader_bp.get("/api/notes")
@jwt_required()
def list_notes():
    if not _admin_only():
        return jsonify({"msg": "접근 권한이 없습니다."}), 403

    notes_dir = _notes_directory()
    if not notes_dir.is_dir():
        return jsonify({"notes": []})

    notes = []
    for candidate in notes_dir.iterdir():
        if _safe_note_path(notes_dir, candidate.name):
            number, title = candidate.stem.split("_", 1)
            notes.append({"name": candidate.name, "title": title, "order": int(number)})

    notes.sort(key=lambda note: (-note["order"], note["name"]))
    return jsonify({"notes": notes})


@notes_reader_bp.get("/api/notes/content")
@jwt_required()
def get_note_content():
    if not _admin_only():
        return jsonify({"msg": "접근 권한이 없습니다."}), 403

    name = request.args.get("name", "")
    note_path = _safe_note_path(_notes_directory(), name)
    if note_path is None:
        return jsonify({"msg": "연구 노트를 찾을 수 없습니다."}), 404

    try:
        with note_path.open("r", encoding="utf-8") as note_file:
            content = note_file.read(MAX_NOTE_BYTES + 1)
    except (OSError, UnicodeError):
        return jsonify({"msg": "연구 노트를 읽을 수 없습니다."}), 500

    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        return jsonify({"msg": "연구 노트의 크기 제한을 초과했습니다."}), 413
    return jsonify({"name": name, "content": content})
