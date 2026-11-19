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

컨테이너 안에서는 `127.0.0.1`이 컨테이너 자신이므로 `DATABASE_URL`·`REDIS_URL`·`MONGODB_URI`에는 DB가 실제로 있는 호스트를 적는다. 배포 대상과 이미지 레지스트리는 아직 정하지 않았다(docs/decisions.md Q-36).
