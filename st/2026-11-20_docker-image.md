# 코드 리뷰: 배포용 컨테이너 이미지 추가

- 날짜: 2026-11-20
- 브랜치: `feature/62_docker-image` (이슈 #62)
- 기능: 없음 (프로젝트 설정)
- 결정: Q-37 추가(열림) — 배포 대상·레지스트리 미정, 이미지만 먼저 만든다

## 배경
배포하려고 애플리케이션을 이미지로 묶어 달라는 요청. 배포 대상과 레지스트리는 정해지지 않아 이미지와 CI 빌드 확인까지만 하고, 나머지는 Q-37으로 남겼다.

## 바뀐 것
- `Dockerfile`: 2단계 빌드. 1단계에서 `uv sync --locked --no-dev --no-install-project`로 `uv.lock` 그대로 운영 의존성만 `/opt/venv`에 설치하고, 2단계는 `python:3.13-slim-bookworm`에 가상환경·`app/`·`migrations/`·`alembic.ini`만 복사한다. non-root(uid 10001)로 `uvicorn app.main:app`을 8000 포트에 띄우고 `/health`로 HEALTHCHECK 한다. 같은 이미지로 `alembic upgrade head`, `python -m app.contests.collect`를 돌릴 수 있다.
- `.dockerignore`: `.env`·`.env.*`가 빌드 컨텍스트에 들어가지 않게 막고, 테스트·문서·리뷰 기록·캐시를 뺀다.
- `.github/workflows/ci.yml`: `image` 잡 추가. 이미지를 빌드하고, 임시 `JWT_SECRET`만 주고 컨테이너를 띄워 `/health` 200을 확인하고, 마이그레이션·수집 진입점이 이미지 안에서 import되는지 본다. 이어서 compose `app` 프로필을 띄워 실제 PostgreSQL에 마이그레이션이 걸리고 API가 healthy가 되는지 확인한다. 기존 잡은 그대로다.
- `docker-compose.yml`: `app` 프로필에 `migrate`(alembic upgrade head 후 종료)와 `api`(migrate 성공 뒤 기동, `127.0.0.1:8000`)를 추가했다. DB 주소는 서비스 이름(`postgres`, `redis`)으로 덮어쓰고 나머지는 `.env`(없어도 됨)에서 읽는다. 프로필이 없으면 기존처럼 DB만 뜬다. (사용자 질문: compose도 고쳐야 하지 않나)
- `README.md`: 빌드·마이그레이션·실행·수집 명령과 컨테이너 안 `127.0.0.1` 주의.
- `docs/decisions.md`: Q-37 추가. (처음엔 Q-36으로 넣었으나 develop에 알림 PR #59의 Q-36이 먼저 들어가 충돌해 Q-37로 바꿨다)

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- MongoDB는 compose에 없다(CI는 mongo:7 서비스, 수집은 Atlas). compose `api`는 `.env`의 `MONGODB_URI`를 그대로 쓰므로 로컬 MongoDB라면 `host.docker.internal`로 적어야 한다.
- 배포 대상이 정해지면(Q-37) 레지스트리 push 워크플로와 운영용 실행 설정(워커 수, 프록시 뒤라면 `--proxy-headers`/`--forwarded-allow-ips`)을 추가한다.
- `uv` 이미지 버전(0.8.15)과 베이스 이미지는 고정 태그다. 보안 패치를 받으려면 주기적으로 올린다.

### 확인한 것
- 명세: 기능 변경 없음. 1차 MVP 범위 밖의 기능을 더하지 않았다.
- 결정: 저장소 분담(ADR-0008)·수집 일정(Q-21, GitHub Actions)은 바꾸지 않았다. 마이그레이션을 기동 때 자동으로 돌리지 않는 이유는 Q-37에 적었다.
- 아키텍처: 앱 코드 변경 없음. import-linter 계약 영향 없음.
- 보안: 비밀 값은 이미지·레이어에 들어가지 않는다(`.dockerignore`, 환경변수로만 주입). 컨테이너는 root가 아닌 사용자로 돈다. CI 스모크 테스트의 `JWT_SECRET`은 실행마다 새로 만든 임시 값이다.
- 시간: 앱은 `zoneinfo`+`tzdata`로 KST를 직접 쓴다. `TZ=Asia/Seoul`은 로그 시각용이다.
- 주석: Dockerfile·.dockerignore·CI 단계에 무엇을·왜 주석을 달았다. Python 코드 변경 없음.
- 테스트: 앱 코드 변경 없음. 이미지 동작은 CI `image` 잡이 확인한다.
- Git 규약: 이슈 #62, `feature/62_docker-image`, 커밋 `⚙️`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` / `uv run ruff check .` | 로컬에 `uv`가 없어 실행하지 못함. CI에서 확인 (Python 코드 변경 없음) |
| `uv run mypy` | 같음 |
| `uv run lint-imports` | 같음 |
| `uv run pytest` | 같음 |
| `docker build` / 컨테이너 `/health` | 로컬에 `docker`가 없어 실행하지 못함. CI `image` 잡에서 확인 |
| `docker compose --profile app up --wait api` | 같음. CI `image` 잡에서 확인 |
| `python scripts/check_conventions.py` | CI에서 확인 |
