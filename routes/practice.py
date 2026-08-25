import json
import io
import re
import zipfile

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from extensions import db, limiter
from models import PracticeProblemFile, PracticeProblemSet, PracticeProblemVariant, User
from services.practice_ai import generate_problem_draft


practice_bp = Blueprint('practice', __name__, url_prefix='/api/practice')

ALLOWED_LANGUAGES = {'Python', 'C#'}
ALLOWED_RUNTIME_PLATFORMS = {'dotnet', 'dotnet_framework'}
ALLOWED_PROJECT_TYPES = {
    'dotnet': {'auto', 'console', 'aspnet_core_mvc', 'aspnet_core_web_api'},
    'dotnet_framework': {'auto', 'console', 'aspnet_mvc5', 'aspnet_web_api2'},
}
ALLOWED_DIFFICULTIES = {'beginner', 'intermediate', 'advanced'}
REQUIRED_TYPES = {'line_selection', 'secure_blank'}
ALLOWED_AI_MODELS = {'gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol'}
FILENAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$')
BLANK_PATTERN = re.compile(r'_{4,}')
MAX_FILES_PER_VARIANT = 20
MAX_CODE_LENGTH = 100_000
MAX_HINT_LENGTH = 5_000
MAX_ANSWER_LENGTH = 20_000
MAX_SCENARIO_LENGTH = 5_000
MAX_EXTRA_REQUEST_LENGTH = 5_000
MAX_SUBMITTED_ANSWERS = 500


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
        answer_line = files[[item['filename'] for item in files].index(filename)]['content'].splitlines()[line - 1]
        if expected_type == 'line_selection':
            stripped_line = answer_line.strip()
            if not stripped_line or stripped_line.startswith(('#', '//')):
                return None, '라인 선택형 정답은 빈 줄이나 주석이 아닌 실행 코드여야 합니다.'
            answer['code'] = stripped_line
        else:
            if len(BLANK_PATTERN.findall(answer_line)) != 1:
                return None, '빈칸형 정답 라인에는 언더바 4개(____)가 필요합니다.'
            answer_text, error = validate_text(raw_answer.get('answer', ''), '빈칸 정답', MAX_ANSWER_LENGTH, required=True)
            if error:
                return None, error
            if '\n' in answer_text or '\r' in answer_text:
                return None, '빈칸 정답에는 줄바꿈을 사용할 수 없습니다.'
            answer['answer'] = answer_text
            answer['answer_kind'] = 'identifier' if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', answer_text) else 'expression'
            answer['completed_line'] = BLANK_PATTERN.sub(answer_text, answer_line, count=1)
        answers.append(answer)

    if not answers:
        return None, '각 문제 유형에 정답을 하나 이상 지정해주세요.'
    return {'problem_type': expected_type, 'hint': hint, 'files': files, 'answers': answers}, None


def serialize_problem_summary(problem_set):
    return {
        'id': problem_set.id,
        'language': problem_set.language,
        'runtime_platform': getattr(problem_set, 'runtime_platform', None),
        'project_type': getattr(problem_set, 'project_type', None),
        'major_topic': problem_set.major_topic,
        'minor_topic': problem_set.minor_topic,
        'difficulty': problem_set.difficulty,
        'creation_method': problem_set.creation_method,
        'status': problem_set.status,
        'created_at': problem_set.created_at.isoformat() if problem_set.created_at else None,
        'updated_at': problem_set.updated_at.isoformat() if problem_set.updated_at else None,
    }


def serialize_public_problem_detail(problem_set):
    variants = []
    for variant in sorted(problem_set.variants, key=lambda item: item.problem_type):
        files = sorted(variant.files, key=lambda item: (item.display_order, getattr(item, 'id', 0) or 0))
        variants.append({
            'problem_type': variant.problem_type,
            'hint': files[0].hint if files else '',
            'files': [
                {'filename': item.filename, 'content': item.content, 'display_order': item.display_order}
                for item in files
            ],
        })
    return {
        **serialize_problem_summary(problem_set),
        'scenario': problem_set.scenario or '',
        'variants': variants,
    }


def serialize_admin_problem_detail(problem_set):
    detail = serialize_public_problem_detail(problem_set)
    variants_by_type = {item['problem_type']: item for item in detail['variants']}
    for variant in problem_set.variants:
        try:
            answers = json.loads(variant.answers_json)
        except (TypeError, json.JSONDecodeError):
            answers = []
        if variant.problem_type in variants_by_type:
            variants_by_type[variant.problem_type]['answers'] = answers
    return detail


def validate_problem_set_payload(data):
    title, error = validate_text(data.get('title', ''), '문제 제목', 255, required=True)
    if error:
        return None, error
    scenario, error = validate_text(data.get('scenario', ''), '문제 설명', 20_000)
    if error:
        return None, error

    language = data.get('language')
    runtime_platform = data.get('runtime_platform')
    project_type = data.get('project_type')
    difficulty = data.get('difficulty')
    major_topic, major_error = validate_text(data.get('major_topic', ''), '대주제', 100, required=True)
    minor_topic, minor_error = validate_text(data.get('minor_topic', ''), '소주제', 255, required=True)
    if language not in ALLOWED_LANGUAGES or difficulty not in ALLOWED_DIFFICULTIES:
        return None, '언어 또는 난이도가 올바르지 않습니다.'
    if language == 'C#':
        if runtime_platform not in ALLOWED_RUNTIME_PLATFORMS:
            return None, 'C# 실행 환경이 올바르지 않습니다.'
        if project_type not in ALLOWED_PROJECT_TYPES[runtime_platform]:
            return None, '선택한 실행 환경에서 지원하지 않는 프로젝트 유형입니다.'
    else:
        runtime_platform = None
        project_type = None
    if major_error or minor_error:
        return None, major_error or minor_error

    raw_variants = data.get('variants')
    if not isinstance(raw_variants, list):
        return None, '문제 유형 데이터가 필요합니다.'
    variant_map = {
        item.get('problem_type'): item
        for item in raw_variants
        if isinstance(item, dict) and item.get('problem_type') in REQUIRED_TYPES
    }
    if set(variant_map) != REQUIRED_TYPES or len(raw_variants) != len(REQUIRED_TYPES):
        return None, '라인 선택형과 빈칸형이 각각 하나씩 필요합니다.'

    validated_variants = []
    for problem_type in ('line_selection', 'secure_blank'):
        variant, variant_error = validate_variant(variant_map[problem_type], problem_type)
        if variant_error:
            return None, variant_error
        validated_variants.append(variant)

    creation_method = data.get('creation_method', 'manual')
    if creation_method not in {'manual', 'ai'}:
        return None, '출제 방식이 올바르지 않습니다.'
    return {
        'title': title,
        'scenario': scenario,
        'language': language,
        'runtime_platform': runtime_platform,
        'project_type': project_type,
        'major_topic': major_topic,
        'minor_topic': minor_topic,
        'difficulty': difficulty,
        'creation_method': creation_method,
        'variants': validated_variants,
    }, None


def append_problem_variants(problem_set, validated_variants):
    for variant_data in validated_variants:
        variant = PracticeProblemVariant(
            problem_type=variant_data['problem_type'],
            answers_json=json.dumps(variant_data['answers'], ensure_ascii=False),
        )
        for file_data in variant_data['files']:
            variant.files.append(PracticeProblemFile(**file_data))
        problem_set.variants.append(variant)


def build_problem_archive(problem_set):
    archive = io.BytesIO()
    folder_names = {
        'line_selection': 'type1_line_selection',
        'secure_blank': 'type2_secure_blank',
    }
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zip_file:
        platform_labels = {'dotnet': '.NET', 'dotnet_framework': '.NET Framework'}
        project_labels = {
            'auto': '자동 선택', 'console': 'Console',
            'aspnet_core_mvc': 'ASP.NET Core MVC',
            'aspnet_core_web_api': 'ASP.NET Core Web API',
            'aspnet_mvc5': 'ASP.NET MVC 5', 'aspnet_web_api2': 'ASP.NET Web API 2',
        }
        metadata_lines = [f'언어: {problem_set.language}']
        if problem_set.language == 'C#':
            runtime_platform = getattr(problem_set, 'runtime_platform', None)
            project_type = getattr(problem_set, 'project_type', None)
            metadata_lines.extend([
                f'실행 환경: {platform_labels.get(runtime_platform, "미지정")}',
                f'프로젝트 유형: {project_labels.get(project_type, "미지정")}',
            ])
        zip_file.writestr('problem_info.txt', '\n'.join(metadata_lines).encode('utf-8-sig'))
        for variant in sorted(problem_set.variants, key=lambda item: item.problem_type):
            folder = folder_names.get(variant.problem_type)
            if not folder:
                continue
            files = sorted(variant.files, key=lambda item: (item.display_order, getattr(item, 'id', 0) or 0))
            for item in files:
                zip_file.writestr(f'{folder}/{item.filename}', item.content.encode('utf-8'))

            hint = files[0].hint if files else ''
            zip_file.writestr(f'{folder}/hint.txt', (hint or '').encode('utf-8-sig'))
            try:
                answers = json.loads(variant.answers_json)
            except (TypeError, json.JSONDecodeError):
                answers = []
            if variant.problem_type == 'line_selection':
                grouped = {}
                for answer in answers:
                    grouped.setdefault(answer.get('filename', ''), []).append(answer.get('line'))
                answer_lines = [
                    f'{filename} - {", ".join(str(line) for line in lines)}번 라인'
                    for filename, lines in grouped.items()
                ]
            else:
                answer_lines = [
                    f'{answer.get("filename", "")} - {answer.get("line")}번 라인 정답: {answer.get("answer", "")}'
                    for answer in answers
                ]
            zip_file.writestr(f'{folder}/answers.txt', '\n'.join(answer_lines).encode('utf-8-sig'))
    archive.seek(0)
    return archive


def _submission_variant_map(raw_variants):
    if not isinstance(raw_variants, list) or len(raw_variants) != len(REQUIRED_TYPES):
        raise ValueError('두 문제 유형의 답안을 모두 제출해주세요.')
    variant_map = {
        item.get('problem_type'): item for item in raw_variants
        if isinstance(item, dict) and item.get('problem_type') in REQUIRED_TYPES
    }
    if set(variant_map) != REQUIRED_TYPES:
        raise ValueError('두 문제 유형의 답안을 모두 제출해주세요.')
    return variant_map


def grade_problem_submission(problem_set, raw_variants):
    submitted_map = _submission_variant_map(raw_variants)
    stored_map = {variant.problem_type: variant for variant in problem_set.variants}
    if set(stored_map) != REQUIRED_TYPES:
        raise ValueError('저장된 문제 유형 구성이 올바르지 않습니다.')

    results = []
    for problem_type in ('line_selection', 'secure_blank'):
        stored_variant = stored_map[problem_type]
        try:
            expected_answers = json.loads(stored_variant.answers_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError('저장된 정답 형식이 올바르지 않습니다.') from error
        submitted_answers = submitted_map[problem_type].get('answers')
        if not isinstance(submitted_answers, list) or len(submitted_answers) > MAX_SUBMITTED_ANSWERS:
            raise ValueError('제출한 정답 형식이 올바르지 않습니다.')

        line_counts = {
            item.filename: max(1, len(item.content.splitlines()))
            for item in stored_variant.files
        }
        submitted_keys = set()
        normalized_answers = []
        for answer in submitted_answers:
            if not isinstance(answer, dict):
                raise ValueError('제출한 정답 형식이 올바르지 않습니다.')
            filename = answer.get('filename')
            line = answer.get('line')
            if filename not in line_counts or isinstance(line, bool) or not isinstance(line, int):
                raise ValueError('제출한 파일명 또는 라인 번호가 올바르지 않습니다.')
            if line < 1 or line > line_counts[filename]:
                raise ValueError('제출한 라인 번호가 코드 범위를 벗어났습니다.')
            key = (filename, line)
            if key in submitted_keys:
                raise ValueError('같은 답안을 중복 제출할 수 없습니다.')
            submitted_keys.add(key)
            normalized = {'filename': filename, 'line': line}
            if problem_type == 'secure_blank':
                answer_text = answer.get('answer')
                if not isinstance(answer_text, str) or len(answer_text) > MAX_ANSWER_LENGTH:
                    raise ValueError('빈칸 정답 형식이 올바르지 않습니다.')
                normalized['answer'] = answer_text.strip()
            normalized_answers.append(normalized)

        if problem_type == 'line_selection':
            expected_keys = {(item['filename'], item['line']) for item in expected_answers}
            is_correct = submitted_keys == expected_keys
            results.append({
                'problem_type': problem_type,
                'correct': is_correct,
                'submitted_count': len(submitted_keys),
                'expected_count': len(expected_keys),
            })
            continue

        expected_map = {(item['filename'], item['line']): item['answer'].strip() for item in expected_answers}
        if any(key not in expected_map for key in submitted_keys):
            raise ValueError('빈칸이 아닌 위치의 정답을 제출할 수 없습니다.')
        submitted_answer_map = {
            (item['filename'], item['line']): item['answer'] for item in normalized_answers
        }
        answer_results = [
            {
                'filename': filename,
                'line': line,
                'correct': submitted_answer_map.get((filename, line)) == expected_answer,
            }
            for (filename, line), expected_answer in expected_map.items()
        ]
        results.append({
            'problem_type': problem_type,
            'correct': all(item['correct'] for item in answer_results),
            'correct_count': sum(1 for item in answer_results if item['correct']),
            'total_count': len(answer_results),
            'answers': answer_results,
        })

    return {'correct': all(item['correct'] for item in results), 'variants': results}


def normalize_generated_blank_answers(raw_variant):
    if not isinstance(raw_variant, dict):
        return raw_variant
    raw_files = raw_variant.get('files')
    raw_answers = raw_variant.get('answers')
    if not isinstance(raw_files, list) or not isinstance(raw_answers, list):
        return raw_variant

    normalized = {
        **raw_variant,
        'files': [dict(item) if isinstance(item, dict) else item for item in raw_files],
        'answers': [dict(item) if isinstance(item, dict) else item for item in raw_answers],
    }
    blank_lines_by_file = {}
    for raw_file in normalized['files']:
        if not isinstance(raw_file, dict) or not isinstance(raw_file.get('filename'), str):
            continue
        content = raw_file.get('content')
        if not isinstance(content, str):
            continue
        normalized_lines = []
        blank_lines = []
        for line_number, line_text in enumerate(content.splitlines(), start=1):
            matches = BLANK_PATTERN.findall(line_text)
            if len(matches) == 1:
                line_text = BLANK_PATTERN.sub('____', line_text)
                blank_lines.append(line_number)
            normalized_lines.append(line_text)
        raw_file['content'] = '\n'.join(normalized_lines)
        blank_lines_by_file[raw_file['filename']] = blank_lines

    for filename, blank_lines in blank_lines_by_file.items():
        answer_indexes = [
            index for index, answer in enumerate(normalized['answers'])
            if isinstance(answer, dict) and answer.get('filename') == filename
        ]
        if not blank_lines or len(answer_indexes) != len(blank_lines):
            continue
        answer_indexes.sort(key=lambda index: (
            normalized['answers'][index].get('line')
            if isinstance(normalized['answers'][index].get('line'), int)
            else float('inf')
        ))
        for answer_index, actual_line in zip(answer_indexes, blank_lines):
            normalized['answers'][answer_index]['line'] = actual_line
    return normalized


def normalize_generated_line_answers(raw_variant):
    if not isinstance(raw_variant, dict):
        return raw_variant
    raw_files = raw_variant.get('files')
    raw_answers = raw_variant.get('answers')
    if not isinstance(raw_files, list) or not isinstance(raw_answers, list):
        return raw_variant

    normalized = {
        **raw_variant,
        'files': [dict(item) if isinstance(item, dict) else item for item in raw_files],
        'answers': [dict(item) if isinstance(item, dict) else item for item in raw_answers],
    }
    lines_by_file = {
        item.get('filename'): item.get('content', '').splitlines()
        for item in normalized['files']
        if isinstance(item, dict) and isinstance(item.get('filename'), str) and isinstance(item.get('content'), str)
    }
    for answer in normalized['answers']:
        if not isinstance(answer, dict):
            continue
        filename = answer.get('filename')
        code = answer.get('code')
        if not isinstance(code, str) or not code.strip() or '\n' in code or '\r' in code:
            raise ValueError('라인 선택형 정답에는 실제 코드 한 줄(code)이 필요합니다.')
        target = code.strip()
        matches = [
            line_number for line_number, line_text in enumerate(lines_by_file.get(filename, []), start=1)
            if line_text.strip() == target
        ]
        if len(matches) != 1:
            raise ValueError('라인 선택형 정답 코드는 파일 안에서 정확히 한 번만 일치해야 합니다.')
        answer['line'] = matches[0]
        answer['code'] = target
    return normalized


def validate_generated_variants(generated, minimum_files):
    raw_variants = generated.get('variants') if isinstance(generated, dict) else None
    if not isinstance(raw_variants, list):
        raise ValueError('AI가 문제 유형 데이터를 반환하지 않았습니다.')
    variant_map = {
        item.get('problem_type'): item for item in raw_variants
        if isinstance(item, dict) and item.get('problem_type') in REQUIRED_TYPES
    }
    if set(variant_map) != REQUIRED_TYPES:
        raise ValueError('AI가 두 문제 유형을 모두 생성하지 않았습니다.')

    validated_variants = []
    for problem_type in ('line_selection', 'secure_blank'):
        raw_variant = variant_map[problem_type]
        if problem_type == 'line_selection':
            raw_variant = normalize_generated_line_answers(raw_variant)
        else:
            raw_variant = normalize_generated_blank_answers(raw_variant)
        variant, error = validate_variant(raw_variant, problem_type)
        if error:
            raise ValueError(error)
        if len(variant['files']) < minimum_files:
            raise ValueError(f'AI가 {problem_type} 유형의 최소 파일 수를 충족하지 않았습니다.')
        if not variant['hint'].strip():
            raise ValueError(f'AI가 {problem_type} 유형의 힌트를 생성하지 않았습니다.')
        if problem_type == 'secure_blank' and any(
            answer.get('answer_kind') != 'identifier' for answer in variant['answers']
        ):
            raise ValueError('AI 빈칸 정답은 함수명, 변수명 또는 상수 같은 단일 식별자여야 합니다.')
        validated_variants.append({
            'problem_type': variant['problem_type'],
            'hint': variant['hint'],
            'files': [{'filename': item['filename'], 'content': item['content']} for item in variant['files']],
            'answers': variant['answers'],
        })
    return validated_variants


@practice_bp.route('/problems/generate', methods=['POST'])
@jwt_required()
@limiter.limit('5 per hour')
def generate_problem_set():
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403

    data = request.get_json(silent=True) or {}
    language = data.get('language')
    runtime_platform = data.get('runtime_platform')
    project_type = data.get('project_type')
    difficulty = data.get('difficulty')
    major_topic, major_error = validate_text(data.get('major_topic', ''), '대주제', 100, required=True)
    minor_topic, minor_error = validate_text(data.get('minor_topic', ''), '소주제', 255, required=True)
    scenario, scenario_error = validate_text(data.get('scenario', ''), '문제 시나리오', MAX_SCENARIO_LENGTH)
    extra_request, extra_error = validate_text(data.get('extra_request', ''), '추가 요청사항', MAX_EXTRA_REQUEST_LENGTH)
    minimum_files = data.get('minimum_files')
    reference_scope = data.get('reference_scope', 'latest')
    model = data.get('model', 'gpt-5.6-luna')

    if language not in ALLOWED_LANGUAGES or difficulty not in ALLOWED_DIFFICULTIES:
        return jsonify({'status': 'error', 'message': '언어 또는 난이도가 올바르지 않습니다.'}), 400
    if language == 'C#':
        if runtime_platform not in ALLOWED_RUNTIME_PLATFORMS:
            return jsonify({'status': 'error', 'message': 'C# 실행 환경이 올바르지 않습니다.'}), 400
        if project_type not in ALLOWED_PROJECT_TYPES[runtime_platform]:
            return jsonify({'status': 'error', 'message': '선택한 실행 환경에서 지원하지 않는 프로젝트 유형입니다.'}), 400
    else:
        runtime_platform = None
        project_type = None
    if major_error or minor_error or scenario_error or extra_error:
        return jsonify({'status': 'error', 'message': major_error or minor_error or scenario_error or extra_error}), 400
    if isinstance(minimum_files, bool) or not isinstance(minimum_files, int) or not 1 <= minimum_files <= MAX_FILES_PER_VARIANT:
        return jsonify({'status': 'error', 'message': f'유형별 최소 파일 수는 1~{MAX_FILES_PER_VARIANT}여야 합니다.'}), 400
    if reference_scope not in {'latest', 'all'}:
        return jsonify({'status': 'error', 'message': '연구노트 범위가 올바르지 않습니다.'}), 400
    if model not in ALLOWED_AI_MODELS:
        return jsonify({'status': 'error', 'message': '지원하지 않는 AI 모델입니다.'}), 400

    try:
        generated = generate_problem_draft(
            language=language,
            runtime_platform=runtime_platform,
            project_type=project_type,
            major_topic=major_topic,
            minor_topic=minor_topic,
            difficulty=difficulty,
            minimum_files=minimum_files,
            scenario=scenario,
            extra_request=extra_request,
            reference_scope=reference_scope,
            model=model,
        )
        validated_variants = validate_generated_variants(generated, minimum_files)
    except ValueError as error:
        current_app.logger.warning('Invalid AI practice problem response: %s', error)
        return jsonify({'status': 'error', 'message': f'AI 생성 결과 검증에 실패했습니다: {error}'}), 422
    except Exception:
        current_app.logger.exception('AI practice problem generation failed')
        return jsonify({'status': 'error', 'message': 'AI 문제 생성에 실패했습니다. 잠시 후 다시 시도해주세요.'}), 502

    return jsonify({'status': 'success', 'data': {'variants': validated_variants}})


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


@practice_bp.route('/public/problems/<int:problem_set_id>', methods=['GET'])
def get_published_problem_set(problem_set_id):
    problem_set = PracticeProblemSet.query.filter_by(id=problem_set_id, status='published').first()
    if not problem_set:
        return jsonify({'status': 'error', 'message': '활성화된 문제를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'data': serialize_public_problem_detail(problem_set)})


@practice_bp.route('/public/problems/<int:problem_set_id>/submit', methods=['POST'])
@jwt_required()
@limiter.limit('30 per minute')
def submit_published_problem_set(problem_set_id):
    problem_set = PracticeProblemSet.query.filter_by(id=problem_set_id, status='published').first()
    if not problem_set:
        return jsonify({'status': 'error', 'message': '활성화된 문제를 찾을 수 없습니다.'}), 404
    data = request.get_json(silent=True) or {}
    try:
        result = grade_problem_submission(problem_set, data.get('variants'))
    except ValueError as error:
        return jsonify({'status': 'error', 'message': str(error)}), 400
    return jsonify({'status': 'success', 'data': result})


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


@practice_bp.route('/problems/<int:problem_set_id>', methods=['GET'])
@jwt_required()
def get_problem_set_for_edit(problem_set_id):
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    problem_set = db.session.get(PracticeProblemSet, problem_set_id)
    if not problem_set:
        return jsonify({'status': 'error', 'message': '문제 세트를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'data': serialize_admin_problem_detail(problem_set)})


@practice_bp.route('/problems/<int:problem_set_id>', methods=['PUT'])
@jwt_required()
def update_problem_set(problem_set_id):
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    problem_set = db.session.get(PracticeProblemSet, problem_set_id)
    if not problem_set:
        return jsonify({'status': 'error', 'message': '문제 세트를 찾을 수 없습니다.'}), 404

    payload, error = validate_problem_set_payload(request.get_json(silent=True) or {})
    if error:
        return jsonify({'status': 'error', 'message': error}), 400

    for field in ('title', 'scenario', 'language', 'runtime_platform', 'project_type', 'major_topic', 'minor_topic', 'difficulty', 'creation_method'):
        setattr(problem_set, field, payload[field])
    try:
        problem_set.variants.clear()
        db.session.flush()
        append_problem_variants(problem_set, payload['variants'])
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Practice problem set update failed')
        return jsonify({'status': 'error', 'message': '문제 세트 수정에 실패했습니다.'}), 500
    return jsonify({'status': 'success', 'data': serialize_problem_summary(problem_set)})


@practice_bp.route('/problems/<int:problem_set_id>/download', methods=['GET'])
@jwt_required()
def download_problem_set(problem_set_id):
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    problem_set = db.session.get(PracticeProblemSet, problem_set_id)
    if not problem_set:
        return jsonify({'status': 'error', 'message': '문제 세트를 찾을 수 없습니다.'}), 404
    archive = build_problem_archive(problem_set)
    return send_file(
        archive,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'practice_problem_{problem_set.id}.zip',
    )


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

    payload, error = validate_problem_set_payload(request.get_json(silent=True) or {})
    if error:
        return jsonify({'status': 'error', 'message': error}), 400

    problem_set = PracticeProblemSet(
        title=payload['title'],
        language=payload['language'],
        runtime_platform=payload['runtime_platform'],
        project_type=payload['project_type'],
        major_topic=payload['major_topic'],
        minor_topic=payload['minor_topic'],
        difficulty=payload['difficulty'],
        scenario=payload['scenario'],
        creation_method=payload['creation_method'],
        status='draft',
        created_by=admin.id,
    )
    append_problem_variants(problem_set, payload['variants'])

    try:
        db.session.add(problem_set)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Practice problem set creation failed')
        return jsonify({'status': 'error', 'message': '문제 세트 저장에 실패했습니다.'}), 500

    return jsonify({'status': 'success', 'data': {'id': problem_set.id, 'status': problem_set.status}}), 201
