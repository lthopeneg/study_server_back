import os
import sys
import tempfile
import subprocess
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import User

notes_bp = Blueprint('notes', __name__)

import platform

# 운영 체제에 따른 기본 경로 설정 (로컬 Windows vs 서버 Linux)
if platform.system() == "Windows":
    DEFAULT_NOTES_PATH = r"C:\Users\user\Desktop\97_연구_노트"
else:
    # 오라클 클라우드 (Linux) 환경의 기본 폴더명 매핑
    DEFAULT_NOTES_PATH = "/home/ubuntu/research_note"

BASE_NOTES_PATH = os.getenv("RESEARCH_NOTES_PATH", DEFAULT_NOTES_PATH)
SECTION_DIRECTORIES = {
    "notes": "Notes",
    "results": "Reports",
}
WEB_TEXT_EXTENSIONS = {".md", ".txt", ".json", ".csv"}

def check_admin_role(login_id):
    user = User.query.filter_by(login_id=login_id).first()
    return user and user.role == 'ADMIN'

@notes_bp.route('/api/notes/section-files', methods=['GET'])
@jwt_required()
def get_section_files():
    current_user_id = get_jwt_identity()
    if not check_admin_role(current_user_id):
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403

    section = request.args.get('section', '').lower()
    directory_name = SECTION_DIRECTORIES.get(section)
    if not directory_name:
        return jsonify({"status": "error", "message": "지원하지 않는 자료 구분입니다."}), 400

    section_dir = os.path.join(BASE_NOTES_PATH, directory_name)
    files = []
    if os.path.isdir(section_dir):
        for root, dirs, filenames in os.walk(section_dir):
            dirs.sort()
            for filename in sorted(filenames):
                if os.path.splitext(filename)[1].lower() not in WEB_TEXT_EXTENSIONS:
                    continue
                full_path = os.path.join(root, filename)
                relative_path = os.path.relpath(full_path, BASE_NOTES_PATH).replace('\\', '/')
                files.append({"name": filename, "path": relative_path})

    return jsonify({
        "status": "success",
        "section": section,
        "files": files,
    })

@notes_bp.route('/api/notes/experiments', methods=['GET'])
@jwt_required()
def get_experiments():
    current_user_id = get_jwt_identity()
    if not check_admin_role(current_user_id):
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403

    exp_dir = os.path.join(BASE_NOTES_PATH, "Experiments")
    experiments = []
    
    if os.path.exists(exp_dir):
        # Experiments 폴더 내부의 디렉토리 목록을 가져옵니다
        for item in os.listdir(exp_dir):
            item_path = os.path.join(exp_dir, item)
            if os.path.isdir(item_path):
                # 생성 시간이나 수정 시간을 가져올 수 있습니다
                # 여기서는 임시로 폴더명을 title과 id로 사용합니다
                experiments.append({
                    "id": item,
                    "title": item.replace('_', ' '),
                    "date": "최근 수정"
                })
    
    return jsonify({
        "status": "success",
        "experiments": experiments
    })

@notes_bp.route('/api/notes/file', methods=['GET'])
@jwt_required()
def get_file_content():
    current_user_id = get_jwt_identity()
    if not check_admin_role(current_user_id):
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403

    file_path_param = request.args.get('path')
    if not file_path_param:
        return jsonify({"status": "error", "message": "파일 경로가 지정되지 않았습니다."}), 400

    # 보안: 상위 폴더 접근 제한
    full_path = os.path.abspath(os.path.join(BASE_NOTES_PATH, file_path_param))
    if not full_path.startswith(os.path.abspath(BASE_NOTES_PATH)):
         return jsonify({"status": "error", "message": "잘못된 접근입니다."}), 403

    if not os.path.exists(full_path):
        return jsonify({"status": "error", "message": "파일을 찾을 수 없습니다."}), 404

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({"status": "success", "content": content})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@notes_bp.route('/api/notes/run', methods=['POST'])
@jwt_required()
def run_python_code():
    current_user_id = get_jwt_identity()
    if not check_admin_role(current_user_id):
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403

    data = request.get_json()
    code = data.get('code')
    if not code:
        return jsonify({"status": "error", "message": "실행할 코드가 없습니다."}), 400

    # 임시 파일 생성 및 코드 작성
    fd, temp_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(code)

        # 현재 실행 중인 파이썬 인터프리터 경로를 사용하여 실행
        result = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=10 # 무한 루프 방지
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\n[에러 출력]\n{result.stderr}"

        return jsonify({
            "status": "success",
            "output": output
        })

    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "실행 시간이 초과되었습니다 (무한 루프 의심)."}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"실행 중 오류 발생: {str(e)}"}), 500
    finally:
        # 실행 후 임시 파일 삭제
        if os.path.exists(temp_path):
            os.remove(temp_path)

@notes_bp.route('/api/notes/experiment_files', methods=['GET'])
@jwt_required()
def get_experiment_files():
    current_user_id = get_jwt_identity()
    if not check_admin_role(current_user_id):
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403

    exp_id = request.args.get('id')
    if not exp_id:
        return jsonify({"status": "error", "message": "실험 ID가 지정되지 않았습니다."}), 400

    exp_dir = os.path.abspath(os.path.join(BASE_NOTES_PATH, "Experiments", exp_id))
    if not exp_dir.startswith(os.path.abspath(os.path.join(BASE_NOTES_PATH, "Experiments"))):
         return jsonify({"status": "error", "message": "잘못된 접근입니다."}), 403

    if not os.path.exists(exp_dir):
        return jsonify({"status": "error", "message": "폴더를 찾을 수 없습니다."}), 404

    file_list = []
    for root, dirs, files in os.walk(exp_dir):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, exp_dir)
            # 윈도우 경로를 url-friendly하게 변경
            file_list.append(rel_path.replace('\\', '/'))

    return jsonify({
        "status": "success",
        "files": file_list
    })

@notes_bp.route('/api/notes/prompts', methods=['GET'])
@jwt_required()
def get_prompts_list():
    current_user_id = get_jwt_identity()
    if not check_admin_role(current_user_id):
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403

    prompts_dir = os.path.abspath(os.path.join(BASE_NOTES_PATH, "Prompts"))
    file_list = []
    
    if os.path.exists(prompts_dir):
        for root, dirs, files in os.walk(prompts_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, prompts_dir)
                file_list.append(rel_path.replace('\\', '/'))

    return jsonify({
        "status": "success",
        "files": file_list
    })
