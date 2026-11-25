# comp

## 컨테이너 이미지

배포용 이미지는 저장소 루트의 `Dockerfile`로 만든다. 운영 의존성만 `uv.lock` 그대로 설치하고, 비밀 값은 이미지에 넣지 않고 실행할 때 환경변수로 준다(필요한 변수는 `.env.example` 참고).

```bash
docker build -t comp .

# 스키마 마이그레이션 (배포마다 API보다 먼저)
docker run --rm --env-file .env.prod comp alembic upgrade head

# API 서버 (컨테이너 8000 포트, GET /health)
docker run -d --env-file .env.prod -p 8000:8000 comp

# 대회 수집 1회 (F2-1)
docker run --rm --env-file .env.prod comp python -m app.contests.collect
```

로컬 DB(`docker-compose.yml`)에 붙여 이미지를 돌려 보려면 `docker compose --profile app up --build`를 쓴다. 마이그레이션(`migrate`)이 끝나면 API(`api`, `127.0.0.1:8000`)가 뜬다. 프로필 없이 `docker compose up`을 하면 지금처럼 PostgreSQL·Redis만 뜬다. compose의 앱 서비스는 DB 주소를 서비스 이름으로 바꿔 넣고, MongoDB는 `.env`의 `MONGODB_URI`를 쓴다(호스트의 MongoDB라면 `localhost` 대신 `host.docker.internal`).

컨테이너 안에서는 `127.0.0.1`이 컨테이너 자신이므로 `DATABASE_URL`·`REDIS_URL`·`MONGODB_URI`에는 DB가 실제로 있는 호스트를 적는다. 이미지는 Docker Hub(`{DOCKERHUB_USERNAME}/comp`)에 올린다. `.github/workflows/image.yml`이 develop에 머지되면 `develop`·`sha-<커밋>` 태그로, 릴리즈 태그(`v1.2.3`)를 push하면 `1.2.3`·`1.2`·`latest` 태그로 자동 push한다. 저장소 Settings > Secrets and variables > Actions에 Variable `DOCKERHUB_USERNAME`과 Secret `DOCKERHUB_TOKEN`(Docker Hub Personal access token, Read & Write)이 있어야 한다. 배포는 아래 'EC2 자동 배포'를 본다(docs/decisions.md Q-37).

```bash
docker pull <도커허브아이디>/comp:latest
```

## EC2 자동 배포

develop에 머지되면 `image.yml`이 이미지를 올리고, 성공하면 `.github/workflows/deploy.yml`이 그 커밋의 이미지(`sha-<7자리>`)를 EC2에 배포한다. 서버에서는 `deploy/deploy.sh`가 `deploy/docker-compose.yml`(PostgreSQL·Redis·migrate·api)로 이미지 받기 → 마이그레이션 → API 교체(healthy 대기) 순서로 돈다. 마이그레이션이 실패하면 API는 이전 버전 그대로다. 롤백은 Actions > deploy > Run workflow에 이전 태그(예: `sha-abc1234`)를 넣는다.

SSH 22 포트는 평소 닫아 두고, 배포하는 동안만 러너 IP를 보안 그룹에 열었다가 닫는다.

### 1. EC2 준비 (한 번)

1. Ubuntu 인스턴스를 만들고 Elastic IP를 붙인다(재시작해도 주소가 그대로여야 `EC2_HOST`·`EC2_KNOWN_HOSTS`가 맞는다).
2. 보안 그룹 인바운드: 22는 **열지 않는다**(배포 때 워크플로가 연다. 직접 접속할 때만 내 IP로 잠깐 연다). API 포트(기본 8000, `.env`의 `API_PORT`)는 열어 둔다.
3. Docker와 compose 플러그인을 설치하고 접속 사용자를 docker 그룹에 넣는다.
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker "$USER" && sudo systemctl enable --now docker
   ```
4. `~/comp/.env`를 만든다(권한 600). `.env.example`의 값에 아래를 더한다. `DATABASE_URL`·`REDIS_URL`은 compose가 서비스 이름으로 덮어쓰므로 적지 않아도 된다.
   ```bash
   # URL 에 그대로 들어가므로 URL-safe 문자만: python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   POSTGRES_PASSWORD=
   API_PORT=8000
   MONGODB_URI=mongodb+srv://...   # Atlas. EC2 의 Elastic IP 를 Atlas IP Access List 에 넣는다
   ```
5. 호스트 키를 받아 둔다(믿을 수 있는 네트워크에서, 또는 EC2 콘솔의 시스템 로그와 지문을 대조): `ssh-keyscan -t ed25519 <EC2_HOST>`

### 2. AWS IAM 사용자 (배포 전용)

액세스 키를 만들고 이 보안 그룹만 고칠 수 있게 한다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"],
      "Resource": "arn:aws:ec2:<리전>:<계정ID>:security-group/<sg-id>"
    },
    { "Effect": "Allow", "Action": "ec2:DescribeSecurityGroupRules", "Resource": "*" }
  ]
}
```

### 3. GitHub 저장소 설정

Settings > Secrets and variables > Actions (image.yml의 `DOCKERHUB_USERNAME`·`DOCKERHUB_TOKEN`은 그대로 쓴다)

| 종류 | 이름 | 값 |
|---|---|---|
| Variable | `EC2_HOST` | Elastic IP 또는 도메인 |
| Variable | `EC2_USER` | 예: `ubuntu` |
| Variable | `EC2_KNOWN_HOSTS` | 1-5의 `ssh-keyscan` 출력 줄 |
| Variable | `AWS_REGION` | 예: `ap-northeast-2` |
| Variable | `EC2_SECURITY_GROUP_ID` | `sg-...` |
| Variable | `HEALTH_URL` | 선택. 배포 뒤 러너가 확인할 주소, 예: `http://<EC2_HOST>:8000/health` |
| Secret | `EC2_SSH_KEY` | EC2 키 페어 개인 키(`.pem` 내용 전체) |
| Secret | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | 2의 IAM 사용자 키 |

설정이 빠지면 deploy 워크플로 첫 단계가 무엇이 없는지 알려 주고 실패한다. 서버에서 직접 상태를 볼 때는 `cd ~/comp && docker compose --env-file .env --env-file release.env ps`(또는 `logs api`).
