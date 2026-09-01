import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from extensions import db
from models import PracticeProblemSet, PracticeProblemSyncState, User
from routes.practice import append_problem_variants, validate_problem_set_payload


LANGUAGE_FOLDERS = {'python': 'Python', 'csharp': 'C#'}
VARIANT_FOLDERS = {
    'type_1': 'line_selection',
    'type_2': 'secure_blank',
}
PROBLEM_FOLDER_PATTERN = re.compile(r'^problem_(\d+)$')


class PracticeRepositoryError(ValueError):
    pass


@dataclass(frozen=True)
class RepositoryProblem:
    source_key: str
    source_revision: str
    problem_id: int | None
    status: str
    payload: dict


def _read_text(path, label):
    try:
        return path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as error:
        raise PracticeRepositoryError(f'{label}을(를) 읽을 수 없습니다: {path}') from error


def _read_json(path, label):
    try:
        return json.loads(_read_text(path, label))
    except json.JSONDecodeError as error:
        raise PracticeRepositoryError(f'{label} JSON 형식이 올바르지 않습니다: {path}') from error


def _variant_file_order(metadata, folder_name, files_directory):
    raw_variants = metadata.get('variants')
    manifest = raw_variants.get(folder_name, {}) if isinstance(raw_variants, dict) else {}
    filenames = manifest.get('files') if isinstance(manifest, dict) else None
    actual_names = {path.name for path in files_directory.iterdir() if path.is_file()}
    if filenames is None:
        return sorted(actual_names)
    if (
        not isinstance(filenames, list)
        or any(not isinstance(name, str) for name in filenames)
        or len(filenames) != len(set(filenames))
        or set(filenames) != actual_names
    ):
        raise PracticeRepositoryError(
            f'problem.json의 {folder_name} 파일 목록과 files 폴더가 일치하지 않습니다.'
        )
    return filenames


def load_repository_problem(problem_directory, root_directory):
    metadata_path = problem_directory / 'problem.json'
    metadata = _read_json(metadata_path, '문제 정보')
    if not isinstance(metadata, dict) or metadata.get('schema_version') != 1:
        raise PracticeRepositoryError(f'지원하지 않는 문제 형식입니다: {metadata_path}')

    language_folder = problem_directory.parent.name
    expected_language = LANGUAGE_FOLDERS.get(language_folder)
    if metadata.get('language') != expected_language:
        raise PracticeRepositoryError(f'언어 폴더와 problem.json의 언어가 다릅니다: {problem_directory}')

    folder_match = PROBLEM_FOLDER_PATTERN.fullmatch(problem_directory.name)
    folder_problem_id = int(folder_match.group(1)) if folder_match else None
    metadata_problem_id = metadata.get('problem_id')
    if metadata_problem_id is not None and (
        isinstance(metadata_problem_id, bool)
        or not isinstance(metadata_problem_id, int)
        or metadata_problem_id < 1
        or metadata_problem_id != folder_problem_id
    ):
        raise PracticeRepositoryError(f'문제 폴더 번호와 problem_id가 일치하지 않습니다: {problem_directory}')

    variants = []
    revision_hasher = hashlib.sha256()
    revision_paths = [metadata_path]
    for folder_name, problem_type in VARIANT_FOLDERS.items():
        variant_directory = problem_directory / folder_name
        files_directory = variant_directory / 'files'
        answers_path = variant_directory / 'answers.json'
        hint_path = variant_directory / 'hint.txt'
        if not files_directory.is_dir() or not answers_path.is_file() or not hint_path.is_file():
            raise PracticeRepositoryError(f'{folder_name} 필수 파일이 누락되었습니다: {problem_directory}')

        filenames = _variant_file_order(metadata, folder_name, files_directory)
        files = []
        for filename in filenames:
            file_path = files_directory / filename
            files.append({'filename': filename, 'content': _read_text(file_path, '코드 파일')})
            revision_paths.append(file_path)
        answers = _read_json(answers_path, '정답')
        hint = _read_text(hint_path, '힌트')
        revision_paths.extend([answers_path, hint_path])
        variants.append({
            'problem_type': problem_type,
            'hint': hint,
            'files': files,
            'answers': answers,
        })

    for path in sorted(revision_paths, key=lambda item: item.as_posix()):
        revision_hasher.update(path.relative_to(problem_directory).as_posix().encode('utf-8'))
        revision_hasher.update(b'\0')
        revision_hasher.update(path.read_bytes())
        revision_hasher.update(b'\0')

    payload_data = {
        key: metadata.get(key)
        for key in (
            'title', 'scenario', 'language', 'runtime_platform', 'project_type',
            'major_topic', 'minor_topic', 'difficulty', 'creation_method',
        )
    }
    payload_data['variants'] = variants
    payload, error = validate_problem_set_payload(payload_data)
    if error:
        raise PracticeRepositoryError(f'{problem_directory}: {error}')
    status = metadata.get('status', 'draft')
    if status not in {'draft', 'published'}:
        raise PracticeRepositoryError(f'공개 상태가 올바르지 않습니다: {problem_directory}')
    return RepositoryProblem(
        source_key=problem_directory.relative_to(root_directory).as_posix(),
        source_revision=revision_hasher.hexdigest(),
        problem_id=metadata_problem_id,
        status=status,
        payload=payload,
    )


def load_practice_repository(root):
    root_directory = Path(root).resolve()
    if not root_directory.is_dir():
        raise PracticeRepositoryError(f'문제 저장소를 찾을 수 없습니다: {root_directory}')
    problems = []
    for language_folder in LANGUAGE_FOLDERS:
        language_directory = root_directory / language_folder
        if not language_directory.exists():
            continue
        if not language_directory.is_dir():
            raise PracticeRepositoryError(f'언어 경로가 폴더가 아닙니다: {language_directory}')
        for problem_directory in sorted(language_directory.iterdir()):
            if not problem_directory.is_dir():
                continue
            if not PROBLEM_FOLDER_PATTERN.fullmatch(problem_directory.name):
                raise PracticeRepositoryError(f'문제 폴더명이 올바르지 않습니다: {problem_directory}')
            problems.append(load_repository_problem(problem_directory, root_directory))
    if not problems:
        raise PracticeRepositoryError('동기화할 문제를 찾을 수 없습니다.')
    source_keys = [problem.source_key for problem in problems]
    if len(source_keys) != len(set(source_keys)):
        raise PracticeRepositoryError('중복된 문제 경로가 있습니다.')
    return problems


def _resolve_admin(admin_login):
    resolved_login = admin_login or os.getenv('PRACTICE_SYNC_ADMIN_LOGIN')
    query = User.query.filter_by(role='ADMIN')
    if resolved_login:
        query = query.filter_by(login_id=resolved_login)
    admin = query.order_by(User.id).first()
    if not admin:
        raise PracticeRepositoryError('새 문제의 작성자로 사용할 관리자 계정을 찾을 수 없습니다.')
    return admin


def _find_existing_problem(repository_problem):
    existing = PracticeProblemSet.query.filter_by(source_key=repository_problem.source_key).first()
    if existing or repository_problem.problem_id is None:
        return existing
    candidate = db.session.get(PracticeProblemSet, repository_problem.problem_id)
    if candidate and candidate.source_key not in {None, repository_problem.source_key}:
        raise PracticeRepositoryError(
            f'문제 번호 {repository_problem.problem_id}가 다른 Git 경로에서 관리되고 있습니다.'
        )
    return candidate


def sync_practice_repository(root, admin_login=None, dry_run=False):
    repository_problems = load_practice_repository(root)
    result = {'created': [], 'updated': [], 'skipped': []}
    admin = None
    try:
        for repository_problem in repository_problems:
            sync_state = db.session.get(PracticeProblemSyncState, repository_problem.source_key)
            if sync_state and sync_state.source_revision == repository_problem.source_revision:
                result['skipped'].append(repository_problem.source_key)
                continue
            problem_set = _find_existing_problem(repository_problem)
            if problem_set and problem_set.source_revision == repository_problem.source_revision:
                db.session.add(PracticeProblemSyncState(
                    source_key=repository_problem.source_key,
                    source_revision=repository_problem.source_revision,
                    last_problem_id=problem_set.id,
                ))
                result['skipped'].append(repository_problem.source_key)
                continue
            payload = repository_problem.payload
            if problem_set is None:
                admin = admin or _resolve_admin(admin_login)
                problem_set = PracticeProblemSet(created_by=admin.id)
                db.session.add(problem_set)
                result['created'].append(repository_problem.source_key)
            else:
                problem_set.variants.clear()
                db.session.flush()
                result['updated'].append(repository_problem.source_key)

            for field in (
                'title', 'language', 'runtime_platform', 'project_type', 'major_topic',
                'minor_topic', 'difficulty', 'scenario', 'creation_method',
            ):
                setattr(problem_set, field, payload[field])
            problem_set.status = repository_problem.status
            problem_set.source_key = repository_problem.source_key
            problem_set.source_revision = repository_problem.source_revision
            problem_set.managed_by = 'git'
            append_problem_variants(problem_set, payload['variants'])
            db.session.flush()
            if sync_state is None:
                sync_state = PracticeProblemSyncState(source_key=repository_problem.source_key)
                db.session.add(sync_state)
            sync_state.source_revision = repository_problem.source_revision
            sync_state.last_problem_id = problem_set.id

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return result
