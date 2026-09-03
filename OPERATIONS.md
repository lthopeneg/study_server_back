# 응답 지연 진단

컨테이너가 running이어도 HTTP 요청을 처리하지 못할 수 있습니다.
재시작으로 회복되었다는 사실만으로 DB나 메모리를 원인으로 확정하지 않습니다.

장애가 재발하면 재시작 **전에** 다음 결과를 확보합니다.

```sh
docker inspect study-back-app --format 'status={{.State.Status}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}'
docker stats --no-stream study-back-app
docker logs --since 10m --tail 300 study-back-app
docker exec study-back-app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/api/check-auth', timeout=5).status)"
```

마지막 명령에서 쿠키 없는 요청의 HTTP 401은 인증 실패지만 **HTTP 응답은 정상 도착**했다는 의미입니다.
TimeoutError는 응답 대기 실패입니다. 로그 공유 전 개인정보와 민감정보를 확인합니다.
서비스 복구가 필요하면 `docker restart study-back-app`을 실행하되 진행 중인 AI 생성 등 요청은 중단됩니다.

## 이번 보완 범위

- 프론트 초기 인증 확인에만 10초 제한과 재시도 화면 적용. AI 생성 제한 시간은 변경하지 않습니다.
- PyMySQL 연결 5초, 소켓 읽기/쓰기 각각 15초, 풀 연결 획득 5초 제한.
- 풀에서 연결을 꺼낼 때 상태 확인, 300초 이상 된 연결은 다음 사용 시 재생성.
- 요청 시작/종료에 같은 ID 기록. 5초 이상 요청은 종료 시 WARNING 기록.
  시작만 있고 종료가 없는 ID로 지연 중인 라우트를 좁힙니다.
  워커의 요청 처리 시작 이전에 막히면 시작 로그도 없습니다.
- 진단 로그에는 경로 템플릿만 기록하고 쿼리·본문·쿠키는 기록하지 않습니다.
  기존 Gunicorn/Flask 로그의 정책은 별도입니다.

이는 총 요청 실행 시간 제한이나 자동 복구 장치가 아닙니다. Gunicorn gthread의
`--timeout 60`도 개별 요청의 60초 종료를 보장하지 않습니다.
외부 API·SMTP 대기, 잠금, 모든 스레드 점유 등의 원인은 추가 진단이 필요합니다.
DB 제한으로 정상 장기 쿼리도 실패할 수 있으므로 배포 후 실제 사용을 확인합니다.
