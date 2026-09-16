# 운영 진입점과 컨테이너 구성

- 공식 주소: `https://scspace.duckdns.org`
- Caddy 컨테이너: `study-caddy`; 호스트 TCP 80/443과 UDP 443 공개
- 프론트엔드: `study-front-app:80`; 외부 포트 미공개
- 백엔드: `study-back-app:5000`; 외부 포트 미공개
- 세 컨테이너는 `study-network`에서 통신합니다.
- `/api/*`는 Caddy가 백엔드로 전달하고 나머지 요청은 프론트엔드로 전달합니다.
- 공인 IP의 HTTP 요청은 공식 HTTPS 주소로 리디렉션합니다.
- MySQL은 별도 서버의 사설 IP로 연결하며 DB 서버의 TCP 3306은 앱 서버 사설 IP `10.0.0.75/32`에서만 허용합니다.
- 로컬 DBeaver 관리는 DB 서버의 SSH 22번 터널을 사용하고 MySQL 3306을 인터넷에 직접 공개하지 않습니다.

GitHub Actions는 배포할 때 `study-network`를 확인하고 새 애플리케이션 컨테이너를 이 네트워크에 연결해야 합니다. Caddy 설정은 서버의 `/home/ubuntu/caddy/Caddyfile`, 인증서와 런타임 데이터는 `study-caddy-data` 및 `study-caddy-config` Docker 볼륨에 있습니다.

예약 뉴스 파이프라인은 공용 GitHub 실행기에서 사설 MySQL로 직접 연결하지 않습니다. GitHub Actions가 앱 서버에 SSH로 접속한 뒤 DB 접근 권한이 있는 `study-back-app` 컨테이너 안에서 수집과 AI 생성을 실행합니다. 따라서 뉴스 작업에는 배포 워크플로와 동일한 `SS_HOST`, `SS_USERNAME`, `SS_KEY` Repository Secret이 필요합니다.

백엔드 컨테이너는 `APP_ENV=production`, `JWT_COOKIE_SECURE=true`,
`JWT_COOKIE_CSRF_PROTECT=true`, `CORS_ORIGINS=https://scspace.duckdns.org`로 실행합니다.
프론트엔드는 같은 출처의 `/api`를 사용하고 변경 요청에 CSRF 헤더를 자동으로 전송합니다.
HTTPS 프록시 없이 Secure 쿠키 설정을 먼저 적용하면 브라우저가 인증 쿠키를 전송하지 않습니다.

기본 상태 확인:

```sh
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
docker network inspect study-network
docker logs --since 10m --tail 200 study-caddy
```

정상 상태에서는 Caddy만 호스트의 80/443을 공개하고 프론트엔드와 백엔드에는 `0.0.0.0` 포트 매핑이 없습니다. Caddy를 재생성할 때 인증서 유지를 위해 기존 두 Docker 볼륨을 다시 마운트합니다.

백엔드 배포 후 DB 연결이 실패하면 실제 `DB_HOST` 값을 출력하지 말고 대상이 사설 주소인지 여부만 확인합니다. GitHub Actions의 `DB_HOST` Repository Secret에는 프로토콜이나 포트가 없는 DB 서버 사설 IP를 저장합니다.

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

# 암호화 백업과 복구

운영 백엔드는 `/home/ubuntu/study-backups`를 컨테이너의 `/backups`에 마운트합니다.
GitHub 저장소의 `BACKUP_ENCRYPTION_KEY` Secret은 충분히 긴 무작위 값으로 등록하고,
GitHub 외의 안전한 비밀 저장소에도 별도 보관해야 합니다. 이 값을 잃으면 기존 백업을
복구할 수 없습니다. Secret 값은 로그나 명령 이력에 출력하지 않습니다.

`Encrypted Database Backup` 작업은 매일 한국시간 오전 3시 20분에 다음 명령을 실행합니다.
백업은 MySQL 일관성 덤프를 gzip으로 압축한 다음 AES-256-CBC와 PBKDF2로 암호화하며
권한을 600으로 설정합니다. 기본 보관 기간은 14일입니다.

```sh
docker exec study-back-app python backup_database.py create
find /home/ubuntu/study-backups -maxdepth 1 -type f -name '*.sql.gz.enc' -printf '%TY-%Tm-%Td %TH:%TM %s %f\n'
```

복구는 데이터를 변경하므로 먼저 별도 DB에서 연습합니다. 백업 파일 크기와 생성 시각을
확인한 다음 복구 대상 DB 이름을 `--confirm`에 정확히 입력합니다.

```sh
docker exec study-back-app python backup_database.py restore /backups/백업파일.sql.gz.enc --confirm 실제_DB_NAME
```

복구 후에는 사용자 수, 최신 뉴스와 문제 수를 확인하고 로그인·스크랩·문제 조회를
검증합니다. 암호화 파일의 존재만으로 복구 가능성을 보장할 수 없으므로 월 1회 별도
복구 DB에서 실제 복구 훈련을 수행합니다.

# 주간 운영 보안 점검

`Production Security Check` 작업은 매주 다음 항목을 읽기 전용으로 검사합니다.

- Caddy, 프론트, 백엔드, Redis 컨테이너 실행 상태
- Caddy만 80/443을 공개하고 앱·Redis는 호스트 포트를 공개하지 않는지 여부
- 운영 모드와 JWT·DB·백업 필수 환경변수의 존재 여부(값은 출력하지 않음)
- 백엔드 인증 API가 5초 안에 응답하는지 여부
- 최근 2일 안의 암호화 백업 존재 여부
- 루트 디스크 사용량 90% 미만 여부

실패하면 GitHub Actions에서 실패 알림을 확인합니다. 이 점검은 OS 패치, SSH 키 관리,
Oracle Cloud NSG와 DB 서버 방화벽을 대신하지 않습니다. 월 1회 다음 항목도 수동 확인합니다.

```sh
sudo ss -lntup
sudo ufw status verbose
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
docker inspect study-back-app --format 'restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
```

애플리케이션 로그를 공유하거나 보관하기 전 쿠키, 토큰, 이메일, 전화번호, API 키와 DB
접속정보가 포함되지 않았는지 확인합니다. 로그인 보안 화면에는 IP와 기기의 원문이 아닌
서버 비밀키 기반 요약값만 표시됩니다.
