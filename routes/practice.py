import json
import re

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from extensions import db
from models import PracticeProblemFile, PracticeProblemSet, PracticeProblemVariant, User


practice_bp = Blueprint('practice', __name__, url_prefix='/api/practice')

ALLOWED_LANGUAGES = {'Python', 'C#'}
ALLOWED_DIFFICULTIES = {'beginner', 'intermediate', 'advanced'}
REQUIRED_TYPES = {'line_selection', 'secure_blank'}
FILENAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$')
MAX_FILES_PER_VARIANT = 20
MAX_CODE_LENGTH = 100_000
MAX_HINT_LENGTH = 5_000
MAX_ANSWER_LENGTH = 20_000


def get_admin_user(login_id):
    user = User.query.filter_by(login_id=login_id).first()
    return user if user and user.role == 'ADMIN' else None


def validate_text(value, field, maximum, required=False):
    if not isinstance(value, str):
        return None, f'{field} 형식이 올바르지 않습니다.'
    value = value.strip() if required else value
    if required and not value:
        return None, f'{field}을(를) 입력해주세요.'
    if len(value) > maximum:
        return None, f'{field}은(는) {maximum:,}자 이하여야 합니다.'
    return value, None


def validate_variant(raw_variant, expected_type):
    if not isinstance(raw_variant, dict) or raw_variant.get('problem_type') != expected_type:
        return None, '두 문제 유형이 모두 필요합니다.'

    hint, error = validate_text(raw_variant.get('hint', ''), '힌트', MAX_HINT_LENGTH)
    if error:
        return None, error

    raw_files = raw_variant.get('files')
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= MAX_FILES_PER_VARIANT:
        return None, f'유형별 파일은 1~{MAX_FILES_PER_VARIANT}개여야 합니다.'

    files = []
    filenames = set()
    line_counts = {}
    for index, raw_file in enumerate(raw_files):
        if not isinstance(raw_file, dict):
            return None, '파일 형식이 올바르지 않습니다.'
        filename = raw_file.get('filename')
        if not isinstance(filename, str) or not FILENAME_PATTERN.fullmatch(filename):
            return None, '파일명은 영문, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다.'
        if filename in filenames:
            return None, '같은 유형 안에서 파일명이 중복될 수 없습니다.'
        filenames.add(filename)

        content, error = validate_text(raw_file.get('content', ''), '코드', MAX_CODE_LENGTH)
        if error:
            return None, error
        files.append({'filename': filename, 'content': content, 'hint': hint, 'display_order': index})
        line_counts[filename] = max(1, len(content.splitlines()))

    raw_answers = raw_variant.get('answers')
    if not isinstance(raw_answers, list):
        return None, '정답 형식이 올바르지 않습니다.'

    answers = []
    seen_answer_keys = set()
    for raw_answer in raw_answers:
        if not isinstance(raw_answer, dict):
            return None, '정답 형식이 올바르지 않습니다.'
        filename = raw_answer.get('filename')
        line = raw_answer.get('line')
        if filename not in filenames or isinstance(line, bool) or not isinstance(line, int):
            return None, '정답의 파일명 또는 라인 번호가 올바르지 않습니다.'
        if line < 1 or line > line_counts[filename]:
            return None, '정답 라인 번호가 코드 범위를 벗어났습니다.'
        answer_key = (filename, line)
        if answer_key in seen_answer_keys:
            return None, '같은 파일과 라인의 정답이 중복되었습니다.'
        seen_answer_keys.add(answer_key)

        answer = {'filename': filename, 'line': line}
        if expected_type == 'secure_blank':
            if '____' not in files[[item['filename'] for item in files].index(filename)]['content'].splitlines()[line - 1]:
                return None, '빈칸형 정답 라인에는 언더바 4개(____)가 필요합니다.'
            answer_text, error = validate_text(raw_answer.get('answer', ''), '빈칸 정답', MAX_ANSWER_LENGTH, required=True)
            if error:
                return None, error
            answer['answer'] = answer_text
        answers.append(answer)

    if not answers:
        return None, '각 문제 유형에 정답을 하나 이상 지정해주세요.'
    return {'problem_type': expected_type, 'hint': hint, 'files': files, 'answers': answers}, None


def serialize_problem_summary(problem_set):
    return {
        'id': problem_set.id,
        'language': problem_set.language,
        'major_topic': problem_set.major_topic,
        'minor_topic': problem_set.minor_topic,
        'difficulty': problem_set.difficulty,
        'creation_method': problem_set.creation_method,
        'status': problem_set.status,
        'created_at': problem_set.created_at.isoformat() if problem_set.created_at else None,
        'updated_at': problem_set.updated_at.isoformat() if problem_set.updated_at else None,
    }


@practice_bp.route('/public/problems', methods=['GET'])
def get_published_problem_sets():
    language = request.args.get('language')
    if language not in ALLOWED_LANGUAGES:
        return jsonify({'status': 'error', 'message': '지원하지 않는 언어입니다.'}), 400

    problem_sets = (
        PracticeProblemSet.query
        .filter_by(language=language, status='published')
        .order_by(PracticeProblemSet.id.desc())
        .all()
    )
    return jsonify({
        'status': 'success',
        'data': [serialize_problem_summary(problem_set) for problem_set in problem_sets],
    })


@practice_bp.route('/problems', methods=['GET'])
@jwt_required()
def get_problem_sets():
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403

    problem_sets = PracticeProblemSet.query.order_by(PracticeProblemSet.id.desc()).all()
    return jsonify({
        'status': 'success',
        'data': [serialize_problem_summary(problem_set) for problem_set in problem_sets],
    })


@practice_bp.route('/problems/<int:problem_set_id>/status', methods=['PATCH'])
@jwt_required()
def update_problem_status(problem_set_id):
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403

    data = request.get_json(silent=True) or {}
    status = data.get('status')
    if status not in {'draft', 'published'}:
        return jsonify({'status': 'error', 'message': '공개 상태가 올바르지 않습니다.'}), 400

    problem_set = db.session.get(PracticeProblemSet, problem_set_id)
    if not problem_set:
        return jsonify({'status': 'error', 'message': '문제 세트를 찾을 수 없습니다.'}), 404

    problem_set.status = status
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Practice problem status update failed')
        return jsonify({'status': 'error', 'message': '공개 상태 변경에 실패했습니다.'}), 500

    return jsonify({'status': 'success', 'data': serialize_problem_summary(problem_set)})


@practice_bp.route('/problems', methods=['POST'])
@jwt_required()
def create_problem_set():
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403

    data = request.get_json(silent=True) or {}
    title, error = validate_text(data.get('title', ''), '문제 제목', 255, required=True)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    scenario, error = validate_text(data.get('scenario', ''), '문제 설명', 20_000)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400

    language = data.get('language')
    difficulty = data.get('difficulty')
    major_topic, major_error = validate_text(data.get('major_topic', ''), '대주제', 100, required=True)
    minor_topic, minor_error = validate_text(data.get('minor_topic', ''), '소주제', 255, required=True)
    if language not in ALLOWED_LANGUAGES or difficulty not in ALLOWED_DIFFICULTIES:
        return jsonify({'status': 'error', 'message': '언어 또는 난이도가 올바르지 않습니다.'}), 400
    if major_error or minor_error:
        return jsonify({'status': 'error', 'message': major_error or minor_error}), 400

    raw_variants = data.get('variants')
    if not isinstance(raw_variants, list):
        return jsonify({'status': 'error', 'message': '문제 유형 데이터가 필요합니다.'}), 400
    variant_map = {
        item.get('problem_type'): item
        for item in raw_variants
        if isinstance(item, dict) and item.get('problem_type') in REQUIRED_TYPES
    }
    if set(variant_map) != REQUIRED_TYPES or len(raw_variants) != len(REQUIRED_TYPES):
        return jsonify({'status': 'error', 'message': '라인 선택형과 빈칸형이 각각 하나씩 필요합니다.'}), 400

    validated_variants = []
    for problem_type in ('line_selection', 'secure_blank'):
        variant, variant_error = validate_variant(variant_map[problem_type], problem_type)
        if variant_error:
            return jsonify({'status': 'error', 'message': variant_error}), 400
        validated_variants.append(variant)

    problem_set = PracticeProblemSet(
        title=title,
        language=language,
        major_topic=major_topic,
        minor_topic=minor_topic,
        difficulty=difficulty,
        scenario=scenario,
        creation_method='manual',
        status='draft',
        created_by=admin.id,
    )
    for variant_data in validated_variants:
        variant = PracticeProblemVariant(
            problem_type=variant_data['problem_type'],
            answers_json=json.dumps(variant_data['answers'], ensure_ascii=False),
        )
        for file_data in variant_data['files']:
            variant.files.append(PracticeProblemFile(**file_data))
        problem_set.variants.append(variant)

    try:
        db.session.add(problem_set)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Practice problem set creation failed')
        return jsonify({'status': 'error', 'message': '문제 세트 저장에 실패했습니다.'}), 500

    return jsonify({'status': 'success', 'data': {'id': problem_set.id, 'status': problem_set.status}}), 201
