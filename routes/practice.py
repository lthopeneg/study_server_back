import ast
import json
import io
import re
import zipfile
from collections import Counter

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from extensions import db, limiter
from models import PracticeProblemFile, PracticeProblemSet, PracticeProblemVariant, User
from services.practice_ai import (
    generate_problem_draft,
    generate_scenario_draft,
    repair_problem_draft,
    review_problem_draft,
)


practice_bp = Blueprint('practice', __name__, url_prefix='/api/practice')

ALLOWED_LANGUAGES = {'Python', 'C#'}
ALLOWED_RUNTIME_PLATFORMS = {'dotnet_framework'}
ALLOWED_PROJECT_TYPES = {
    'dotnet_framework': {'aspnet_mvc5', 'aspnet_web_api2'},
}
ALLOWED_DIFFICULTIES = {'beginner', 'intermediate', 'advanced'}
REQUIRED_TYPES = {'line_selection', 'secure_blank'}
LINE_ANSWER_ROLES = {'source', 'validation_failure', 'sink'}
ALLOWED_AI_MODELS = {'gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol'}
FILENAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$')
BLANK_PATTERN = re.compile(r'_{4,}')
MAX_SOURCE_FILES_PER_VARIANT = 20
MAX_TARGET_BLANK_COUNT = 20
MAX_FILES_PER_VARIANT = 24
MAX_CODE_LENGTH = 100_000
MAX_HINT_LENGTH = 5_000
MAX_ANSWER_LENGTH = 20_000
MAX_SCENARIO_LENGTH = 5_000
MAX_EXTRA_REQUEST_LENGTH = 5_000
MAX_SUBMITTED_ANSWERS = 500
MAX_BULK_DELETE_PROBLEMS = 100


def validate_csharp_environment(language, runtime_platform, project_type, request_text='', allow_auto=False):
    if language != 'C#':
        return None
    if runtime_platform not in ALLOWED_RUNTIME_PLATFORMS:
        return 'C# 문제는 .NET Framework 환경만 지원합니다.'
    auto_allowed = allow_auto and runtime_platform == 'dotnet_framework' and project_type == 'auto'
    if not auto_allowed and project_type not in ALLOWED_PROJECT_TYPES[runtime_platform]:
        return '선택한 실행 환경에서 지원하는 프로젝트 유형을 선택해주세요.'

    normalized_request = request_text.casefold() if isinstance(request_text, str) else ''
    requests_framework = '.net framework' in normalized_request or '닷넷 프레임워크' in normalized_request
    if requests_framework and runtime_platform != 'dotnet_framework':
        return '시나리오에는 .NET Framework가 지정되어 있지만 실행 환경은 .NET으로 선택되었습니다.'
    return None


def resolve_generated_project_type(generated, language, runtime_platform, requested_project_type):
    if language != 'C#':
        return None
    if requested_project_type != 'auto':
        return requested_project_type
    generated_project_type = generated.get('project_type') if isinstance(generated, dict) else None
    allowed_auto_types = {'aspnet_mvc5', 'aspnet_web_api2'}
    if runtime_platform != 'dotnet_framework' or generated_project_type not in allowed_auto_types:
        raise ValueError('AI가 .NET Framework 프로젝트 유형을 올바르게 선택하지 않았습니다.')
    return generated_project_type


def detect_csharp_web_project_type(variants):
    detected_types = []
    for variant in variants:
        source = '\n'.join(file['content'] for file in variant['files'] if file['filename'].lower().endswith('.cs'))
        has_mvc = bool(re.search(r'\bSystem\.Web\.Mvc\b|:\s*Controller\b', source))
        has_web_api = bool(re.search(r'\bSystem\.Web\.Http\b|:\s*ApiController\b', source))
        if has_mvc == has_web_api:
            raise ValueError('C# 코드는 ASP.NET MVC 5 또는 ASP.NET Web API 2 구조 중 하나를 명확히 포함해야 합니다.')
        detected_types.append('aspnet_mvc5' if has_mvc else 'aspnet_web_api2')
    if len(set(detected_types)) != 1:
        raise ValueError('두 문제 유형에 서로 다른 .NET Framework 웹 프로젝트 유형이 사용되었습니다.')
    return detected_types[0]


def validate_generated_csharp_project_type(variants, project_type):
    if detect_csharp_web_project_type(variants) != project_type:
        raise ValueError('AI가 선택한 .NET Framework 프로젝트 유형과 실제 코드 구조가 일치하지 않습니다.')


def _csharp_project_template(source_filenames, project_type):
    if project_type == 'aspnet_web_api2':
        references = '''
    <Reference Include="System.Web.Http">
      <HintPath>..\\packages\\Microsoft.AspNet.WebApi.Core.5.2.9\\lib\\net45\\System.Web.Http.dll</HintPath>
    </Reference>
    <Reference Include="System.Net.Http.Formatting">
      <HintPath>..\\packages\\Microsoft.AspNet.WebApi.Client.5.2.9\\lib\\net45\\System.Net.Http.Formatting.dll</HintPath>
    </Reference>
    <Reference Include="Newtonsoft.Json">
      <HintPath>..\\packages\\Newtonsoft.Json.13.0.3\\lib\\net45\\Newtonsoft.Json.dll</HintPath>
    </Reference>'''
        package_items = '''  <package id="Microsoft.AspNet.WebApi.Client" version="5.2.9" targetFramework="net472" />
  <package id="Microsoft.AspNet.WebApi.Core" version="5.2.9" targetFramework="net472" />
  <package id="Newtonsoft.Json" version="13.0.3" targetFramework="net472" />'''
    else:
        references = '''
    <Reference Include="System.Web.Mvc">
      <HintPath>..\\packages\\Microsoft.AspNet.Mvc.5.2.9\\lib\\net45\\System.Web.Mvc.dll</HintPath>
    </Reference>'''
        package_items = '''  <package id="Microsoft.AspNet.Mvc" version="5.2.9" targetFramework="net472" />
  <package id="Microsoft.AspNet.Razor" version="3.2.9" targetFramework="net472" />
  <package id="Microsoft.AspNet.WebPages" version="3.2.9" targetFramework="net472" />
  <package id="Microsoft.Web.Infrastructure" version="2.0.1" targetFramework="net472" />'''
    compile_items = '\n'.join(f'    <Compile Include="{filename}" />' for filename in source_filenames)
    csproj = f'''<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="15.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <Configuration Condition=" '$(Configuration)' == '' ">Debug</Configuration>
    <Platform Condition=" '$(Platform)' == '' ">AnyCPU</Platform>
    <OutputType>Library</OutputType>
    <RootNamespace>PracticeProblem</RootNamespace>
    <AssemblyName>PracticeProblem</AssemblyName>
    <TargetFrameworkVersion>v4.7.2</TargetFrameworkVersion>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="System" />
    <Reference Include="System.Core" />
    <Reference Include="System.Web" />
    <Reference Include="System.Net.Http" />{references}
  </ItemGroup>
  <ItemGroup>
{compile_items}
  </ItemGroup>
  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />
</Project>'''
    packages = f'''<?xml version="1.0" encoding="utf-8"?>
<packages>
{package_items}
</packages>'''
    web_config = '''<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.web>
    <compilation debug="false" targetFramework="4.7.2" />
    <httpRuntime targetFramework="4.7.2" />
  </system.web>
</configuration>'''
    return [
        {'filename': 'PracticeProblem.csproj', 'content': csproj},
        {'filename': 'packages.config', 'content': packages},
        {'filename': 'Web.config', 'content': web_config},
    ]


def apply_csharp_project_templates(generated, project_type):
    raw_variants = generated.get('variants') if isinstance(generated, dict) else None
    if not isinstance(raw_variants, list):
        return generated
    support_names = {'packages.config', 'web.config'}
    for variant in raw_variants:
        files = variant.get('files') if isinstance(variant, dict) else None
        if not isinstance(files, list):
            continue
        source_filenames = [
            file.get('filename') for file in files
            if isinstance(file, dict) and isinstance(file.get('filename'), str)
            and file['filename'].lower().endswith('.cs')
        ]
        variant['files'] = [
            file for file in files
            if not (
                isinstance(file, dict) and isinstance(file.get('filename'), str)
                and (file['filename'].lower().endswith('.csproj') or file['filename'].lower() in support_names)
            )
        ] + _csharp_project_template(source_filenames, project_type)
    return generated


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


def validate_delete_problem_ids(raw_ids):
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= MAX_BULK_DELETE_PROBLEMS:
        raise ValueError(f'삭제할 문제 번호는 1~{MAX_BULK_DELETE_PROBLEMS}개여야 합니다.')
    if any(isinstance(problem_id, bool) or not isinstance(problem_id, int) or problem_id < 1 for problem_id in raw_ids):
        raise ValueError('삭제할 문제 번호 형식이 올바르지 않습니다.')
    if len(set(raw_ids)) != len(raw_ids):
        raise ValueError('삭제할 문제 번호를 중복해서 지정할 수 없습니다.')
    return raw_ids


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
            if raw_answer.get('role') in LINE_ANSWER_ROLES:
                answer['role'] = raw_answer['role']
        else:
            if len(BLANK_PATTERN.findall(answer_line)) != 1:
                return None, '빈칸형 정답 라인에는 언더바 4개(____)가 필요합니다.'
            answer_text, error = validate_text(raw_answer.get('answer', ''), '빈칸 정답', MAX_ANSWER_LENGTH, required=True)
            if error:
                return None, error
            if '\n' in answer_text or '\r' in answer_text:
                return None, '빈칸 정답에는 줄바꿈을 사용할 수 없습니다.'
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', answer_text):
                return None, (
                    '빈칸 정답은 함수명, 메서드명, 변수명, 상수 또는 클래스명 같은 '
                    '영문 단일 식별자여야 합니다.'
                )
            answer['answer'] = answer_text
            answer['answer_kind'] = 'identifier'
            answer['completed_line'] = BLANK_PATTERN.sub(answer_text, answer_line, count=1)
        answers.append(answer)

    if not answers:
        return None, '각 문제 유형에 정답을 하나 이상 지정해주세요.'
    return {'problem_type': expected_type, 'hint': hint, 'files': files, 'answers': answers}, None


def build_recoverable_generation_draft(generated):
    if not isinstance(generated, dict) or not isinstance(generated.get('variants'), list):
        return None
    variants = []
    for raw_variant in generated['variants']:
        if not isinstance(raw_variant, dict) or raw_variant.get('problem_type') not in REQUIRED_TYPES:
            continue
        files = [
            {'filename': item.get('filename', ''), 'content': item.get('content', '')}
            for item in raw_variant.get('files', [])
            if isinstance(item, dict)
            and isinstance(item.get('filename'), str)
            and isinstance(item.get('content'), str)
        ]
        answers = []
        for item in raw_variant.get('answers', []):
            if not isinstance(item, dict):
                continue
            answer = {
                key: item[key] for key in ('filename', 'line', 'code', 'role', 'answer', 'hint')
                if key in item and isinstance(item[key], (str, int)) and not isinstance(item[key], bool)
            }
            if isinstance(answer.get('filename'), str) and isinstance(answer.get('line'), int):
                answers.append(answer)
        variants.append({
            'problem_type': raw_variant['problem_type'],
            'hint': raw_variant.get('hint', '') if isinstance(raw_variant.get('hint'), str) else '',
            'files': files,
            'answers': answers,
        })
    if not variants:
        return None
    return {'project_type': generated.get('project_type'), 'variants': variants}


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
        runtime_platform = 'dotnet_framework'
        if project_type not in {None, 'auto', 'aspnet_mvc5', 'aspnet_web_api2'}:
            return None, 'C# 프로젝트 유형은 ASP.NET MVC 5 또는 ASP.NET Web API 2여야 합니다.'
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

    if language == 'C#':
        try:
            detected_project_type = detect_csharp_web_project_type(validated_variants)
        except ValueError as error:
            return None, str(error)
        if project_type not in {None, 'auto'} and project_type != detected_project_type:
            return None, '저장된 프로젝트 유형과 실제 C# 코드 구조가 일치하지 않습니다.'
        project_type = detected_project_type

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
        'line_selection': 'type_1',
        'secure_blank': 'type_2',
    }
    language_folders = {'Python': 'python', 'C#': 'csharp'}
    language_folder = language_folders.get(problem_set.language, problem_set.language.lower())
    problem_folder = f'practice_problems/{language_folder}/problem_{problem_set.id:04d}'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zip_file:
        platform_labels = {'dotnet': '.NET', 'dotnet_framework': '.NET Framework'}
        project_labels = {
            'auto': '자동 선택', 'console': 'Console',
            'aspnet_core_mvc': 'ASP.NET Core MVC',
            'aspnet_core_web_api': 'ASP.NET Core Web API',
            'aspnet_mvc5': 'ASP.NET MVC 5', 'aspnet_web_api2': 'ASP.NET Web API 2',
        }
        metadata_lines = [
            f'언어: {problem_set.language}',
            f'대주제: {problem_set.major_topic}',
            f'소주제: {problem_set.minor_topic}',
        ]
        if problem_set.language == 'C#':
            runtime_platform = getattr(problem_set, 'runtime_platform', None)
            project_type = getattr(problem_set, 'project_type', None)
            metadata_lines.extend([
                f'실행 환경: {platform_labels.get(runtime_platform, "미지정")}',
                f'프로젝트 유형: {project_labels.get(project_type, "미지정")}',
            ])
        zip_file.writestr(
            f'{problem_folder}/problem_info.txt',
            '\n'.join(metadata_lines).encode('utf-8-sig'),
        )
        problem_metadata = {
            'schema_version': 1,
            'problem_id': problem_set.id,
            'title': problem_set.title,
            'language': problem_set.language,
            'runtime_platform': getattr(problem_set, 'runtime_platform', None),
            'project_type': getattr(problem_set, 'project_type', None),
            'major_topic': problem_set.major_topic,
            'minor_topic': problem_set.minor_topic,
            'difficulty': problem_set.difficulty,
            'status': problem_set.status,
            'creation_method': problem_set.creation_method,
            'scenario': problem_set.scenario or '',
            'variants': {
                folder_names.get(variant.problem_type): {
                    'problem_type': variant.problem_type,
                    'files': [
                        item.filename for item in sorted(
                            variant.files,
                            key=lambda file_item: (
                                file_item.display_order,
                                getattr(file_item, 'id', 0) or 0,
                            ),
                        )
                    ],
                }
                for variant in problem_set.variants
                if folder_names.get(variant.problem_type)
            },
        }
        zip_file.writestr(
            f'{problem_folder}/problem.json',
            json.dumps(problem_metadata, ensure_ascii=False, indent=2).encode('utf-8'),
        )
        for variant in sorted(problem_set.variants, key=lambda item: item.problem_type):
            folder = folder_names.get(variant.problem_type)
            if not folder:
                continue
            variant_folder = f'{problem_folder}/{folder}'
            files = sorted(variant.files, key=lambda item: (item.display_order, getattr(item, 'id', 0) or 0))
            for item in files:
                zip_file.writestr(f'{variant_folder}/files/{item.filename}', item.content.encode('utf-8'))

            hint = files[0].hint if files else ''
            zip_file.writestr(f'{variant_folder}/hint.txt', (hint or '').encode('utf-8-sig'))
            try:
                answers = json.loads(variant.answers_json)
            except (TypeError, json.JSONDecodeError):
                answers = []
            zip_file.writestr(
                f'{variant_folder}/answers.json',
                json.dumps(answers, ensure_ascii=False, indent=2).encode('utf-8'),
            )
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
            zip_file.writestr(
                f'{variant_folder}/answers.txt',
                '\n'.join(answer_lines).encode('utf-8-sig'),
            )
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
            answer_results = [
                {
                    'filename': item['filename'],
                    'line': item['line'],
                    'correct': (item['filename'], item['line']) in expected_keys,
                }
                for item in normalized_answers
            ]
            results.append({
                'problem_type': problem_type,
                'correct': is_correct,
                'submitted_count': len(submitted_keys),
                'expected_count': len(expected_keys),
                'correct_count': sum(1 for item in answer_results if item['correct']),
                'answers': answer_results,
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
    unique_answers = []
    seen_exact_answers = set()
    for answer in normalized['answers']:
        if not isinstance(answer, dict):
            unique_answers.append(answer)
            continue
        exact_key = (
            answer.get('filename'), answer.get('line'),
            answer.get('answer'), answer.get('hint'),
        )
        if exact_key in seen_exact_answers:
            continue
        seen_exact_answers.add(exact_key)
        unique_answers.append(answer)
    normalized['answers'] = unique_answers
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

    answer_by_location = {}
    deduplicated_answers = []
    for answer in normalized['answers']:
        if not isinstance(answer, dict):
            deduplicated_answers.append(answer)
            continue
        location = (answer.get('filename'), answer.get('line'))
        previous = answer_by_location.get(location)
        if previous is None:
            answer_by_location[location] = answer
            deduplicated_answers.append(answer)
            continue
        if previous.get('answer') == answer.get('answer') and previous.get('hint') == answer.get('hint'):
            continue
        raise ValueError(
            f'2유형 정답 위치가 충돌합니다: {location[0]} {location[1]}번 라인에 서로 다른 정답이 있습니다.'
        )
    normalized['answers'] = deduplicated_answers
    return normalized


def blank_ordinal(index):
    labels = {1: '첫 번째', 2: '두 번째', 3: '세 번째'}
    return labels.get(index, f'{index}번째')


def fallback_blank_hint(line_text):
    if re.search(r'\.\s*_{4,}\s*\(', line_text):
        return (
            '빈칸 앞 객체의 자료형과 이 호출 뒤에 사용되는 값을 함께 살펴보세요. '
            '현재 보안 처리 단계에 필요한 동작을 제공하는 메서드 이름을 입력합니다.'
        )
    if re.search(r'_{4,}\s*\(', line_text):
        return (
            '괄호 안에 전달되는 값들과 이 줄의 결과가 다음 단계에서 사용되는 방식을 살펴보세요. '
            '현재 보안 조치를 직접 수행하는 함수 또는 메서드 이름을 입력합니다.'
        )
    return (
        '같은 파일의 선언부와 빈칸 전후 코드에서 이 값이 사용되는 위치를 비교해보세요. '
        '현재 보안 조치를 완성하는 변수, 상수 또는 형식 이름을 입력합니다.'
    )


def build_generated_blank_hint(raw_variant):
    if not isinstance(raw_variant, dict):
        raise ValueError('2유형 힌트 형식이 올바르지 않습니다.')
    raw_files = raw_variant.get('files')
    raw_answers = raw_variant.get('answers')
    if not isinstance(raw_files, list) or not isinstance(raw_answers, list):
        raise ValueError('2유형 파일 또는 정답 형식이 올바르지 않습니다.')

    blank_locations = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            continue
        filename = raw_file.get('filename')
        content = raw_file.get('content')
        if not isinstance(filename, str) or not isinstance(content, str):
            continue
        occurrence = 0
        for line_number, line_text in enumerate(content.splitlines(), start=1):
            if len(BLANK_PATTERN.findall(line_text)) == 1:
                occurrence += 1
                blank_locations.append((filename, line_number, occurrence, line_text))

    answer_by_location = {}
    for answer in raw_answers:
        if not isinstance(answer, dict):
            raise ValueError('2유형 정답 형식이 올바르지 않습니다.')
        key = (answer.get('filename'), answer.get('line'))
        if key in answer_by_location:
            raise ValueError('2유형의 같은 빈칸에 정답이 중복되었습니다.')
        answer_by_location[key] = answer

    blank_keys = {(filename, line) for filename, line, _, _ in blank_locations}
    if blank_keys != set(answer_by_location):
        raise ValueError('2유형의 모든 빈칸에는 정답과 개별 힌트가 하나씩 필요합니다.')

    sections = []
    normalized_answers = [dict(answer) for answer in raw_answers]
    normalized_answer_by_location = {
        (answer.get('filename'), answer.get('line')): answer for answer in normalized_answers
    }
    for filename, line, occurrence, line_text in blank_locations:
        answer = normalized_answer_by_location[(filename, line)]
        hint_text, error = validate_text(answer.get('hint', ''), '빈칸별 힌트', 1_000, required=True)
        if error:
            hint_text = fallback_blank_hint(line_text)
        answer_text = str(answer.get('answer') or '').strip()
        if len(answer_text) >= 3 and re.search(
            rf'(?<!\w){re.escape(answer_text)}(?!\w)', hint_text, re.IGNORECASE,
        ):
            hint_text = fallback_blank_hint(line_text)
        answer['hint'] = hint_text
        sections.append(
            f'- {filename} ({blank_ordinal(occurrence)} 빈칸)\n  {hint_text}'
        )

    return {**raw_variant, 'hint': '\n\n'.join(sections), 'answers': normalized_answers}


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
    answer_by_location = {}
    deduplicated_answers = []
    for answer in normalized['answers']:
        if not isinstance(answer, dict):
            deduplicated_answers.append(answer)
            continue
        location = (answer.get('filename'), answer.get('line'))
        previous = answer_by_location.get(location)
        if previous is None:
            answer_by_location[location] = answer
            deduplicated_answers.append(answer)
            continue
        if previous.get('code') == answer.get('code') and previous.get('role') == answer.get('role'):
            continue
        raise ValueError(
            f'1유형 정답 역할이 충돌합니다: {location[0]} {location[1]}번 라인에 서로 다른 역할이 있습니다.'
        )
    normalized['answers'] = deduplicated_answers
    return normalized


def validate_generated_line_hint(raw_variant):
    hint = raw_variant.get('hint') if isinstance(raw_variant, dict) else None
    answers = raw_variant.get('answers') if isinstance(raw_variant, dict) else None
    if not isinstance(hint, str) or not isinstance(answers, list):
        raise ValueError('1유형 힌트 또는 정답 형식이 올바르지 않습니다.')

    header_pattern = re.compile(
        r'^\s*-\s+.+?\s+\((Source|Validation Failure|Sink)\)\s*$',
        re.MULTILINE,
    )
    headers = list(header_pattern.finditer(hint))
    expected_labels = ['Source', 'Validation Failure', 'Sink']
    if [match.group(1) for match in headers] != expected_labels:
        raise ValueError('1유형 힌트는 Source, Validation Failure, Sink 순서의 세 구간이 필요합니다.')

    role_by_label = {
        'Source': 'source',
        'Validation Failure': 'validation_failure',
        'Sink': 'sink',
    }
    hinted_counts = {}
    for index, header in enumerate(headers):
        section_end = headers[index + 1].start() if index + 1 < len(headers) else len(hint)
        section = hint[header.end():section_end]
        count_match = re.search(r'^\s*정답 라인 수\s*:\s*(\d+)\s*개\s*$', section, re.MULTILINE)
        if not count_match:
            raise ValueError(f'1유형 {header.group(1)} 힌트에 정답 라인 수가 필요합니다.')
        explanation = section[:count_match.start()].strip()
        if not explanation:
            raise ValueError(f'1유형 {header.group(1)} 힌트에 설명이 필요합니다.')
        hinted_counts[role_by_label[header.group(1)]] = int(count_match.group(1))

    answer_roles = []
    for answer in answers:
        role = answer.get('role') if isinstance(answer, dict) else None
        if role not in LINE_ANSWER_ROLES:
            raise ValueError('1유형의 모든 정답에는 source, validation_failure, sink 역할이 필요합니다.')
        answer_roles.append(role)
    actual_counts = Counter(answer_roles)
    missing_roles = [role for role in LINE_ANSWER_ROLES if actual_counts[role] < 1]
    if missing_roles:
        raise ValueError('1유형은 Source, Validation Failure, Sink 정답을 각각 최소 1개 포함해야 합니다.')
    if any(hinted_counts[role] != actual_counts[role] for role in LINE_ANSWER_ROLES):
        raise ValueError('1유형 힌트의 정답 라인 수와 역할별 실제 정답 수가 일치하지 않습니다.')


def validate_generated_variants(generated, minimum_files, language='Python', minor_topic=''):
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
            validate_generated_line_hint(raw_variant)
        else:
            raw_variant = normalize_generated_blank_answers(raw_variant)
            raw_variant = build_generated_blank_hint(raw_variant)
        variant, error = validate_variant(raw_variant, problem_type)
        if error:
            raise ValueError(error)
        source_extension = '.py' if language == 'Python' else '.cs'
        source_file_count = sum(
            1 for item in variant['files'] if item['filename'].lower().endswith(source_extension)
        )
        if source_file_count < minimum_files:
            raise ValueError(f'AI가 {problem_type} 유형의 최소 파일 수를 충족하지 않았습니다.')
        if language == 'C#' and not any(
            item['filename'].lower().endswith('.csproj') for item in variant['files']
        ):
            raise ValueError(f'AI가 {problem_type} 유형의 C# 프로젝트 파일(.csproj)을 생성하지 않았습니다.')
        if not variant['hint'].strip():
            raise ValueError(f'AI가 {problem_type} 유형의 힌트를 생성하지 않았습니다.')
        if problem_type == 'secure_blank':
            invalid_answers = [
                answer for answer in variant['answers']
                if answer.get('answer_kind') != 'identifier'
            ]
            if invalid_answers:
                raise ValueError(
                    'AI 빈칸 정답은 함수명, 메서드명, 변수명, 상수 또는 클래스명 같은 단일 식별자여야 합니다.'
                )
        validated_variants.append({
            'problem_type': variant['problem_type'],
            'hint': variant['hint'],
            'files': [{'filename': item['filename'], 'content': item['content']} for item in variant['files']],
            'answers': variant['answers'],
        })
    return validated_variants


def _completed_variant_file_content(variant, file):
    lines = file['content'].split('\n')
    for answer in variant.get('answers', []):
        if answer.get('filename') != file['filename']:
            continue
        line_index = answer.get('line', 0) - 1
        if 0 <= line_index < len(lines) and '____' in lines[line_index]:
            lines[line_index] = lines[line_index].replace('____', answer.get('answer', ''), 1)
    return '\n'.join(lines)


def _variant_source(variant, extension):
    return '\n'.join(
        _completed_variant_file_content(variant, file)
        for file in variant['files'] if file['filename'].lower().endswith(extension)
    )


def validate_memory_buffer_security(validated_variants, language, minor_topic):
    if '메모리 버퍼 오버플로우' not in minor_topic:
        return
    secure_variant = next(
        variant for variant in validated_variants if variant['problem_type'] == 'secure_blank'
    )
    if language == 'Python':
        found_sink = False
        found_safe_sink = False
        for file in secure_variant['files']:
            if not file['filename'].lower().endswith('.py'):
                continue
            source = _completed_variant_file_content(secure_variant, file).replace('____', 'BLANK_ANSWER')
            try:
                tree = ast.parse(source)
            except SyntaxError as error:
                raise ValueError(f'2유형 Python 코드 구문을 해석할 수 없습니다: {file["filename"]}') from error
            bounded_names = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                    continue
                if not isinstance(node.value.func, ast.Name) or node.value.func.id != 'min' or len(node.value.args) < 3:
                    continue
                has_source_length = any(
                    isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == 'len'
                    for argument in node.value.args for child in ast.walk(argument)
                )
                has_capacity = any(
                    isinstance(child, ast.Name) and any(token in child.id.casefold() for token in ('buffer', 'capacity', 'limit'))
                    for argument in node.value.args for child in ast.walk(argument)
                )
                if has_source_length and has_capacity:
                    bounded_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {'memmove', 'memcpy'}:
                    continue
                found_sink = True
                if len(node.args) >= 3 and isinstance(node.args[2], ast.Name) and node.args[2].id in bounded_names:
                    found_safe_sink = True
        if not found_sink:
            raise ValueError('2유형에 ctypes 메모리 복사 Sink가 필요합니다.')
        if not found_safe_sink:
            raise ValueError('2유형 ctypes 복사 크기는 요청 길이, 실제 원본 길이, 대상 버퍼 용량을 모두 제한한 값이어야 합니다.')
        return

    source = _variant_source(secure_variant, '.cs')
    required_patterns = {
        '음수 길이 검사': r'\b\w*(?:length|size)\w*\s*<\s*0',
        '실제 배열 길이 검사': r'\.Length\b',
        '버퍼 용량 검사': r'\b\w*(?:length|size)\w*\s*>\s*\w*(?:buffer|capacity|limit)\w*',
        '비관리 메모리 복사': r'\bMarshal\.Copy\s*\(',
        '비관리 메모리 해제': r'\bfinally\b[\s\S]*?\bMarshal\.FreeHGlobal\s*\(',
    }
    missing = [label for label, pattern in required_patterns.items() if not re.search(pattern, source, re.IGNORECASE)]
    if 'Convert.FromBase64String' in source and not re.search(r'catch\s*\(\s*FormatException\b', source):
        missing.append('잘못된 Base64 입력 처리')
    if missing:
        raise ValueError(f'2유형 C# 메모리 안전 조건이 누락되었습니다: {", ".join(missing)}')


def validate_memory_buffer_answer_quality(validated_variants, language, minor_topic):
    if language != 'Python' or '메모리 버퍼 오버플로우' not in minor_topic:
        return
    variant_map = {variant['problem_type']: variant for variant in validated_variants}
    line_variant = variant_map.get('line_selection')
    blank_variant = variant_map.get('secure_blank')
    if not line_variant or not blank_variant:
        return

    source_target_pattern = re.compile(
        r'(?:data|packet|payload|size|length|buffer|content|body|file|image|input|request|bytes|packaging)',
        re.IGNORECASE,
    )
    memory_validation_pattern = re.compile(
        r'(?:size|length|len\s*\(|capacity|buffer|packet|payload|data|bytes|copy|mem)',
        re.IGNORECASE,
    )
    for answer in line_variant['answers']:
        code = answer.get('code', '')
        role = answer.get('role')
        if role == 'source':
            assignment = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', code)
            if assignment and not source_target_pattern.search(assignment.group(1)):
                raise ValueError(
                    f'1유형 관련 없는 Source 정답입니다: {answer["filename"]} {answer["line"]}번 라인의 '
                    f'`{assignment.group(1)}` 값은 메모리 복사 데이터 흐름과 직접 관련되어야 합니다.'
                )
        elif role == 'validation_failure' and not memory_validation_pattern.search(code):
            raise ValueError(
                f'1유형 관련 없는 Validation Failure 정답입니다: {answer["filename"]} '
                f'{answer["line"]}번 라인은 복사 길이·원본 길이·버퍼 용량 검증과 직접 관련되어야 합니다.'
            )
        elif role == 'sink' and not re.search(r'ctypes\s*\.\s*(?:memmove|memcpy)\s*\(', code):
            raise ValueError(
                f'1유형 Sink 정답이 실제 ctypes 메모리 복사 호출이 아닙니다: '
                f'{answer["filename"]} {answer["line"]}번 라인.'
            )

    first_answer = blank_variant['answers'][0] if blank_variant['answers'] else None
    if first_answer:
        first_file = next(
            (file for file in blank_variant['files'] if file['filename'] == first_answer['filename']), None,
        )
        first_line = ''
        if first_file:
            lines = first_file['content'].splitlines()
            if 1 <= first_answer['line'] <= len(lines):
                first_line = lines[first_answer['line'] - 1]
        if not re.search(r'^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*_{4,}\s*\(', first_line):
            raise ValueError(
                f'2유형 첫 번째 빈칸은 안전한 복사 크기를 결정하는 핵심 함수 호출이어야 합니다: '
                f'{first_answer["filename"]} {first_answer["line"]}번 라인.'
            )

    generic_answers = {'isinstance', 'encode', 'decode'}
    for answer in blank_variant['answers']:
        if str(answer.get('answer', '')).casefold() in generic_answers:
            raise ValueError(
                f'2유형의 {answer["filename"]} {answer["line"]}번 빈칸은 메모리 경계의 핵심 보안 조치보다 '
                f'일반 처리 함수 `{answer["answer"]}`를 정답으로 사용하고 있습니다.'
            )


def validate_python_generated_syntax(validated_variants, language):
    if language != 'Python':
        return
    for variant in validated_variants:
        for file in variant['files']:
            if not file['filename'].lower().endswith('.py'):
                continue
            source = _completed_variant_file_content(variant, file)
            try:
                ast.parse(source)
            except SyntaxError as error:
                raise ValueError(
                    f'{variant["problem_type"]}의 {file["filename"]} Python 구문이 올바르지 않습니다.'
                ) from error


def build_variant_consistency_check(validated_variants, language):
    variant_map = {variant['problem_type']: variant for variant in validated_variants}
    extension = '.py' if language == 'Python' else '.cs'
    line_files = {
        file['filename'] for file in variant_map['line_selection']['files']
        if file['filename'].lower().endswith(extension)
    }
    blank_files = {
        file['filename'] for file in variant_map['secure_blank']['files']
        if file['filename'].lower().endswith(extension)
    }
    filename_ratio = len(line_files & blank_files) / max(1, len(line_files | blank_files))
    identifier_pattern = re.compile(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b')
    ignored = {
        'import', 'from', 'return', 'class', 'public', 'private', 'static', 'using', 'namespace',
        'string', 'int', 'byte', 'void', 'true', 'false', 'none', 'null', 'self',
    }
    identifier_sets = []
    for problem_type in ('line_selection', 'secure_blank'):
        source = _variant_source(variant_map[problem_type], extension)
        identifier_sets.append({
            token for token in identifier_pattern.findall(source) if token.casefold() not in ignored
        })
    identifier_ratio = len(identifier_sets[0] & identifier_sets[1]) / max(1, len(identifier_sets[0] | identifier_sets[1]))
    status = 'passed' if filename_ratio >= 0.7 and identifier_ratio >= 0.35 else 'warning'
    return {
        'key': 'consistency',
        'label': '1·2유형 코드 일관성',
        'status': status,
        'message': (
            f'소스 파일명 유사도 {filename_ratio:.0%}, 주요 식별자 유사도 {identifier_ratio:.0%}입니다.'
        ),
    }


def validate_csharp_project_dependencies(validated_variants, project_type):
    if project_type not in {'aspnet_mvc5', 'aspnet_web_api2'}:
        return
    package_id = 'Microsoft.AspNet.Mvc' if project_type == 'aspnet_mvc5' else 'Microsoft.AspNet.WebApi.Core'
    assembly = 'System.Web.Mvc' if project_type == 'aspnet_mvc5' else 'System.Web.Http'
    for variant in validated_variants:
        csproj = next(
            (file['content'] for file in variant['files'] if file['filename'].lower().endswith('.csproj')),
            '',
        )
        packages = '\n'.join(
            file['content'] for file in variant['files'] if file['filename'].lower() == 'packages.config'
        )
        if re.search(r'<Project\s+Sdk=', csproj, re.IGNORECASE) or not re.search(
            r'<TargetFrameworkVersion>v4\.[0-9.]+</TargetFrameworkVersion>', csproj,
        ):
            raise ValueError('C# 프로젝트는 기존 형식의 .NET Framework 대상 .csproj여야 합니다.')
        if assembly not in csproj or '<HintPath>' not in csproj or package_id not in packages:
            raise ValueError(f'C# 프로젝트에 복원 가능한 {package_id} 패키지와 .csproj HintPath가 필요합니다.')


def build_generation_quality_report(
    validated_variants,
    target_blank_count,
    minor_topic='',
    language='Python',
    repair_attempted=False,
    ai_review=None,
):
    blank_variant = next(variant for variant in validated_variants if variant['problem_type'] == 'secure_blank')
    blank_count = len(blank_variant['answers'])
    exposed_answers = []
    for answer in blank_variant['answers']:
        answer_text = answer.get('answer', '')
        occurrences = sum(
            file['content'].count(answer_text) for file in blank_variant['files'] if answer_text
        )
        if occurrences:
            exposed_answers.append(f'{answer["filename"]}:{answer["line"]}')
    checks = [
        {'key': 'structure', 'label': '파일·힌트·정답 형식', 'status': 'passed', 'message': '서버 형식 검사를 통과했습니다.'},
        {
            'key': 'security',
            'label': '2유형 보안 필수 조건',
            'status': 'passed' if '메모리 버퍼 오버플로우' in minor_topic else 'warning',
            'message': (
                '메모리 복사의 원본 길이·대상 용량 등 필수 안전 조건을 확인했습니다.'
                if '메모리 버퍼 오버플로우' in minor_topic
                else '이 소주제의 전용 의미 검사는 아직 없으며 공통 형식 검사만 적용되었습니다.'
            ),
        },
        {
            'key': 'blank_count',
            'label': '2유형 목표 빈칸 수',
            'status': 'passed' if blank_count >= target_blank_count else 'warning',
            'message': f'목표 {target_blank_count}개 중 {blank_count}개가 생성되었습니다.',
        },
        {
            'key': 'answer_exposure',
            'label': '빈칸 정답 노출',
            'status': 'warning' if exposed_answers else 'passed',
            'message': (
                f'다른 코드에 동일한 정답이 보이는 위치가 있습니다: {", ".join(exposed_answers)}'
                if exposed_answers else '코드에서 동일한 정답 문자열의 직접 노출을 발견하지 못했습니다.'
            ),
        },
        build_variant_consistency_check(validated_variants, language),
    ]
    if ai_review:
        review_status = 'warning' if ai_review['warnings'] else 'passed'
        checks.append({
            'key': 'ai_review',
            'label': 'AI 독립 검수',
            'status': review_status,
            'message': ai_review['summary'] + (
                f' 개선 의견: {" / ".join(ai_review["warnings"])}' if ai_review['warnings'] else ''
            ),
        })
    deductions = sum(10 for check in checks if check['status'] == 'warning')
    deterministic_score = max(0, 100 - deductions)
    ai_score = ai_review['score'] if ai_review else deterministic_score
    return {
        'status': 'warning' if any(check['status'] == 'warning' for check in checks) else 'passed',
        'repair_attempted': repair_attempted,
        'score': round((deterministic_score + ai_score) / 2),
        'checks': checks,
    }


def build_generation_warnings(validated_variants, target_blank_count):
    blank_variant = next(
        (variant for variant in validated_variants if variant['problem_type'] == 'secure_blank'),
        None,
    )
    actual_blank_count = len(blank_variant['answers']) if blank_variant else 0
    if actual_blank_count >= target_blank_count:
        return []
    return [
        f'2유형 목표 빈칸 수는 {target_blank_count}개지만 의미 있는 빈칸을 '
        f'{actual_blank_count}개 생성했습니다. 저장 전에 내용을 검토해주세요.'
    ]


@practice_bp.route('/problems/generate-scenario', methods=['OPTIONS'])
@limiter.exempt
def generate_problem_scenario_options():
    return '', 204


@practice_bp.route('/problems/generate-scenario', methods=['POST'], provide_automatic_options=False)
@jwt_required()
@limiter.limit('10 per hour')
def generate_problem_scenario():
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403

    data = request.get_json(silent=True) or {}
    language = data.get('language')
    difficulty = data.get('difficulty')
    major_topic, major_error = validate_text(data.get('major_topic', ''), '대주제', 100, required=True)
    minor_topic, minor_error = validate_text(data.get('minor_topic', ''), '소주제', 255, required=True)
    scenario_seed, scenario_error = validate_text(data.get('scenario', ''), '문제 시나리오', MAX_SCENARIO_LENGTH)
    extra_request_seed, extra_error = validate_text(
        data.get('extra_request', ''), '추가 요청사항', MAX_EXTRA_REQUEST_LENGTH,
    )
    minimum_files = data.get('minimum_files')
    target_blank_count = data.get('target_blank_count', 3)
    reference_scope = data.get('reference_scope', 'latest')
    model = data.get('model', 'gpt-5.6-luna')

    if language not in ALLOWED_LANGUAGES or difficulty not in ALLOWED_DIFFICULTIES:
        return jsonify({'status': 'error', 'message': '언어 또는 난이도가 올바르지 않습니다.'}), 400
    if major_error or minor_error or scenario_error or extra_error:
        return jsonify({'status': 'error', 'message': major_error or minor_error or scenario_error or extra_error}), 400
    if isinstance(minimum_files, bool) or not isinstance(minimum_files, int) or not 1 <= minimum_files <= MAX_SOURCE_FILES_PER_VARIANT:
        return jsonify({'status': 'error', 'message': f'유형별 최소 파일 수는 1~{MAX_SOURCE_FILES_PER_VARIANT}여야 합니다.'}), 400
    if isinstance(target_blank_count, bool) or not isinstance(target_blank_count, int) or not 1 <= target_blank_count <= MAX_TARGET_BLANK_COUNT:
        return jsonify({'status': 'error', 'message': f'2유형 목표 빈칸 수는 1~{MAX_TARGET_BLANK_COUNT}여야 합니다.'}), 400
    if reference_scope not in {'latest', 'all'} or model not in ALLOWED_AI_MODELS:
        return jsonify({'status': 'error', 'message': '연구노트 범위 또는 AI 모델이 올바르지 않습니다.'}), 400

    try:
        draft = generate_scenario_draft(
            language=language,
            major_topic=major_topic,
            minor_topic=minor_topic,
            difficulty=difficulty,
            minimum_files=minimum_files,
            target_blank_count=target_blank_count,
            scenario_seed=scenario_seed,
            extra_request_seed=extra_request_seed,
            reference_scope=reference_scope,
            model=model,
        )
    except Exception:
        current_app.logger.exception('AI practice scenario generation failed')
        return jsonify({'status': 'error', 'message': 'AI 시나리오 작성에 실패했습니다. 잠시 후 다시 시도해주세요.'}), 502

    return jsonify({'status': 'success', 'data': draft})


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
    target_blank_count = data.get('target_blank_count', 3)
    reference_scope = data.get('reference_scope', 'latest')
    model = data.get('model', 'gpt-5.6-luna')
    repair_draft = data.get('repair_draft')
    repair_error, repair_error_validation = validate_text(
        data.get('repair_error', ''), '이전 검증 오류', 2_000,
        required=repair_draft is not None,
    )

    if language not in ALLOWED_LANGUAGES or difficulty not in ALLOWED_DIFFICULTIES:
        return jsonify({'status': 'error', 'message': '언어 또는 난이도가 올바르지 않습니다.'}), 400
    if language == 'C#':
        runtime_platform = 'dotnet_framework'
        project_type = 'auto'
    else:
        runtime_platform = None
        project_type = None
    environment_error = validate_csharp_environment(
        language, runtime_platform, project_type, f'{scenario}\n{extra_request}', allow_auto=True,
    )
    if environment_error:
        return jsonify({'status': 'error', 'message': environment_error}), 400
    if major_error or minor_error or scenario_error or extra_error:
        return jsonify({'status': 'error', 'message': major_error or minor_error or scenario_error or extra_error}), 400
    if isinstance(minimum_files, bool) or not isinstance(minimum_files, int) or not 1 <= minimum_files <= MAX_SOURCE_FILES_PER_VARIANT:
        return jsonify({'status': 'error', 'message': f'유형별 최소 파일 수는 1~{MAX_SOURCE_FILES_PER_VARIANT}여야 합니다.'}), 400
    if isinstance(target_blank_count, bool) or not isinstance(target_blank_count, int) or not 1 <= target_blank_count <= MAX_TARGET_BLANK_COUNT:
        return jsonify({'status': 'error', 'message': f'2유형 목표 빈칸 수는 1~{MAX_TARGET_BLANK_COUNT}여야 합니다.'}), 400
    if reference_scope not in {'latest', 'all'}:
        return jsonify({'status': 'error', 'message': '연구노트 범위가 올바르지 않습니다.'}), 400
    if model not in ALLOWED_AI_MODELS:
        return jsonify({'status': 'error', 'message': '지원하지 않는 AI 모델입니다.'}), 400
    if repair_error_validation:
        return jsonify({'status': 'error', 'message': repair_error_validation}), 400
    if repair_draft is not None:
        if not isinstance(repair_draft, dict):
            return jsonify({'status': 'error', 'message': '자동 수정할 AI 초안 형식이 올바르지 않습니다.'}), 400
        if len(json.dumps(repair_draft, ensure_ascii=False)) > 500_000:
            return jsonify({'status': 'error', 'message': '자동 수정할 AI 초안이 너무 큽니다.'}), 400

    generation_conditions = {
        'language': language,
        'runtime_platform': runtime_platform,
        'project_type': project_type,
        'major_topic': major_topic,
        'minor_topic': minor_topic,
        'difficulty': difficulty,
        'minimum_files': minimum_files,
        'target_blank_count': target_blank_count,
        'scenario': scenario,
        'extra_request': extra_request,
        'reference_scope': reference_scope,
        'model': model,
    }
    generated = None
    repair_attempted = repair_draft is not None
    try:
        def validate_candidate(candidate):
            candidate_project_type = resolve_generated_project_type(
                candidate, language, runtime_platform, project_type,
            )
            if language == 'C#':
                apply_csharp_project_templates(candidate, candidate_project_type)
            candidate_variants = validate_generated_variants(
                candidate, minimum_files, language, minor_topic,
            )
            validate_python_generated_syntax(candidate_variants, language)
            validate_memory_buffer_security(candidate_variants, language, minor_topic)
            validate_memory_buffer_answer_quality(candidate_variants, language, minor_topic)
            if language == 'C#':
                validate_generated_csharp_project_type(candidate_variants, candidate_project_type)
                validate_csharp_project_dependencies(candidate_variants, candidate_project_type)
            return candidate_project_type, candidate_variants

        if repair_draft is not None:
            generated = repair_problem_draft(
                repair_draft, repair_error, **generation_conditions,
            )
        else:
            generated = generate_problem_draft(**generation_conditions)
        try:
            resolved_project_type, validated_variants = validate_candidate(generated)
        except ValueError as validation_error:
            if repair_attempted:
                raise
            current_app.logger.warning(
                'AI practice problem quality validation failed; attempting repair: %s', validation_error,
            )
            generated = repair_problem_draft(
                generated, str(validation_error), **generation_conditions,
            )
            repair_attempted = True
            resolved_project_type, validated_variants = validate_candidate(generated)
        review_target = {'project_type': resolved_project_type, 'variants': validated_variants}
        ai_review = review_problem_draft(review_target, **generation_conditions)
        if ai_review['blocking_issues']:
            if repair_attempted:
                raise ValueError(
                    'AI 독립 검수에서 자동 수정 후에도 중대 문제가 발견되었습니다: '
                    + ' / '.join(ai_review['blocking_issues'])
                )
            generated = repair_problem_draft(
                review_target,
                'AI 독립 검수 중대 문제: ' + ' / '.join(ai_review['blocking_issues']),
                **generation_conditions,
            )
            repair_attempted = True
            resolved_project_type, validated_variants = validate_candidate(generated)
            ai_review = {
                'score': min(ai_review['score'], 80),
                'blocking_issues': [],
                'warnings': [
                    *ai_review['warnings'],
                    'AI 독립 검수의 중대 문제를 자동 수정했습니다. 최종 내용은 관리자가 확인해주세요.',
                ],
                'summary': '독립 검수에서 발견된 중대 문제를 자동 수정하고 서버 검사를 다시 통과했습니다.',
            }
        warnings = build_generation_warnings(validated_variants, target_blank_count)
        if repair_attempted:
            warnings.insert(0, '초기 생성 결과의 품질 문제를 감지해 AI 자동 수정 1회를 적용했습니다.')
        quality_report = build_generation_quality_report(
            validated_variants,
            target_blank_count,
            minor_topic,
            language,
            repair_attempted,
            ai_review,
        )
    except ValueError as error:
        current_app.logger.warning('Invalid AI practice problem response: %s', error)
        recoverable_draft = build_recoverable_generation_draft(generated)
        return jsonify({
            'status': 'error',
            'message': f'AI 생성 결과 검증에 실패했습니다: {error}',
            'data': {
                'draft': recoverable_draft,
                'validation_error': str(error),
                'repair_attempted': repair_attempted,
                'can_retry_repair': recoverable_draft is not None,
            },
        }), 422
    except Exception:
        current_app.logger.exception('AI practice problem generation failed')
        return jsonify({'status': 'error', 'message': 'AI 문제 생성에 실패했습니다. 잠시 후 다시 시도해주세요.'}), 502

    return jsonify({'status': 'success', 'data': {
        'variants': validated_variants,
        'warnings': warnings,
        'project_type': resolved_project_type,
        'quality_report': quality_report,
    }})


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


@practice_bp.route('/problems/<int:problem_set_id>', methods=['DELETE'])
@jwt_required()
def delete_problem_set(problem_set_id):
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    problem_set = db.session.get(PracticeProblemSet, problem_set_id)
    if not problem_set:
        return jsonify({'status': 'error', 'message': '문제 세트를 찾을 수 없습니다.'}), 404
    try:
        db.session.delete(problem_set)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Practice problem set deletion failed')
        return jsonify({'status': 'error', 'message': '문제 세트 삭제에 실패했습니다.'}), 500
    return jsonify({'status': 'success', 'data': {'deleted_ids': [problem_set_id]}})


@practice_bp.route('/problems/delete-batch', methods=['POST'])
@jwt_required()
def delete_problem_sets_batch():
    admin = get_admin_user(get_jwt_identity())
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    try:
        problem_ids = validate_delete_problem_ids((request.get_json(silent=True) or {}).get('problem_ids'))
    except ValueError as error:
        return jsonify({'status': 'error', 'message': str(error)}), 400

    problem_sets = PracticeProblemSet.query.filter(PracticeProblemSet.id.in_(problem_ids)).all()
    found_ids = {problem_set.id for problem_set in problem_sets}
    missing_ids = [problem_id for problem_id in problem_ids if problem_id not in found_ids]
    if missing_ids:
        return jsonify({'status': 'error', 'message': '일부 문제 세트를 찾을 수 없습니다.'}), 404
    try:
        for problem_set in problem_sets:
            db.session.delete(problem_set)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Practice problem set batch deletion failed')
        return jsonify({'status': 'error', 'message': '문제 세트 일괄 삭제에 실패했습니다.'}), 500
    return jsonify({'status': 'success', 'data': {'deleted_ids': problem_ids}})


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
