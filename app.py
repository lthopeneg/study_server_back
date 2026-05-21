from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
# 프론트엔드(5173 또는 80포트)의 API 접근을 허용합니다.
CORS(app)

# 프론트엔드에서 요청할 로그인 엔드포인트
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    user_id = data.get('userId')
    password = data.get('password')
    
    # 향후 MySQL DB 연동 전까지 작동할 임시(Mock) 로그인 로직
    if user_id and password:
        return jsonify({
            "status": "success", 
            "username": user_id, 
            "message": f"{user_id}님 환영합니다!"
        }), 200
        
    return jsonify({
        "status": "error", 
        "message": "아이디와 비밀번호를 모두 입력해주세요."
    }), 400

if __name__ == '__main__':
    # host='0.0.0.0'은 추후 도커 환경에서 외부 접속을 받기 위해 필수입니다.
    app.run(host='0.0.0.0', port=5000, debug=True)
