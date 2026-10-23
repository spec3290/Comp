# AGENTS.md — 대회 수집 서비스 (Contest Collector)

에이전트가 가장 먼저 읽는 파일이다. 규칙의 단일 출처이면서 나머지 하네스 요소를 조정하는 지도다. (`CLAUDE.md`는 이 파일을 가리킨다.)

## 프로젝트

여러 사이트에 흩어진 대회를 수집·중복 제거·자격 분류해 보여 주는 백엔드.
스택: Python 3.13+, uv, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL, PyJWT, httpx, pytest.
현재 범위는 **1차 MVP 13개 기능**이다. 2차 이후는 구현하지 않는다.

## 하네스 구성

| 요소 | 위치 | 역할 |
|---|---|---|
| 에이전트 지침 | `AGENTS.md` | 규칙, 우선순위, 스킬 라우팅 |
| 기능 명세 | [docs/features.md](docs/features.md) | 1차~추후 전 기능, 수용 기준, 진행 상태 |
| 결정 내역 | [docs/decisions.md](docs/decisions.md) | 확정 결정(ADR), API·데이터 모델 규약, 결정이 필요한 항목(Q-번호) |
| 스킬 | `.claude/skills/` | `feature-implementation`, `git-workflow`, `code-review` |
| 소스 | `app/`, `tests/` | 아래 아키텍처 제약을 따른다 |
| 검증 | `.github/workflows/ci.yml` | 린트·타입·아키텍처·테스트·Git 규약을 CI가 검증 ([ADR-0006](docs/decisions.md)) |

## 조정 규칙

1. **우선순위**: CI 검증 결과 > features.md 수용 기준 > decisions.md > 이 파일의 서술. 충돌하면 같은 변경에서 낮은 쪽을 고친다.
2. **모호하면 추측하지 않는다.** decisions.md의 `결정이 필요한 항목`을 먼저 확인하고, 없으면 Q-번호로 추가한 뒤 사용자에게 묻는다.
3. **제약을 약화하지 않는다.** CI를 통과시키려고 린트 설정·import-linter 계약·테스트를 완화하거나 삭제하지 않는다. 필요하면 decisions.md에 ADR을 먼저 쓰고 사용자 승인을 받는다.
4. **스킬 라우팅**: 기능 구현은 `feature-implementation`, 커밋·브랜치·이슈·PR·버전은 `git-workflow`, 리뷰는 `code-review`. 스킬은 절차만 담고 규칙은 여기와 docs가 가진다.
5. **같은 실수가 2번 나오면** 문서가 아니라 CI(린트 규칙, import-linter 계약, 테스트)로 올린다.

## 아키텍처 제약

기능 단위 패키지 + 얇은 3계층 구조다 ([ADR-0007](docs/decisions.md)).

```
app/
  main.py            # FastAPI 앱 조립, 라우터 등록
  health.py
  core/              # config, time(KST), db, errors 등 기능 공통
  contests/          # router.py → service.py → repository.py, models.py, rules.py, crawlers/
  auth/              # router.py → service.py → repository.py, models.py, oauth.py
```

- 기능 안의 호출 방향은 `router → service → repository`. 순수 도메인 규칙(상태·D-day·중복 제거·자격 분류)은 `rules.py`에 둔다.
- Protocol은 구현체가 여러 개인 경계에만 쓴다: 크롤러(`contests/crawlers/base.py`의 `ContestSource`), OAuth(`auth/oauth.py`의 `OAuthProvider`).
- 계약의 실체는 `pyproject.toml`의 `[tool.importlinter]`이며 CI가 검사한다.

| ID | 규칙 |
|---|---|
| AC-1 | `app.*.rules`는 `sqlalchemy`, `pymongo`, `fastapi`, `starlette`, `httpx`, `jwt`를 import하지 않는다 |
| AC-2 | `app.*.router`는 `app.*.repository`를 직접 import하지 않는다 (service 경유) |
| AC-3 | `app.core`는 기능 패키지(`app.contests`, `app.auth`)를 import하지 않는다 |

위반은 import를 지우거나 우회하지 말고 로직을 올바른 계층으로 옮겨 해결한다.
코드 규칙은 ruff와 mypy가 강제한다: 타입 주석 필수(strict), naive `datetime` 금지, `print` 금지, `requests` 금지(httpx만), 함수 복잡도 10 이하.

## 피드백 루프 (CI)

PR을 올리면 `.github/workflows/ci.yml`이 다음을 검증한다. 통과 기준은 CI 결과다.

| 검사 | 명령 |
|---|---|
| 포맷·린트 | `uv run ruff format --check .` / `uv run ruff check .` |
| 타입 | `uv run mypy` |
| 아키텍처 | `uv run lint-imports` |
| 테스트 | `uv run pytest` |
| Git 규약 | `python scripts/check_conventions.py` (커밋 메시지, 브랜치명, PR 제목) |

- 로컬에서 위 명령을 먼저 돌려 보는 것은 권장이지만 필수는 아니다. 실패하면 로그를 읽고 원인을 고친다.
- CI가 실패한 PR은 머지하지 않는다. 같은 실패가 3번 반복되면 멈추고 시도한 것과 로그를 사용자에게 보고한다.

## 하드 규칙

- 시간은 KST 기준의 timezone-aware `datetime`만 쓴다. 접수 상태·D-day는 저장하지 않고 날짜로 계산한다.
- 비밀 값은 환경변수로만 다룬다. `.env`를 읽거나 커밋하지 않고, 로그에 토큰·개인정보를 남기지 않는다.
- 에러 응답은 [ADR-0004](docs/decisions.md)의 단일 포맷을 따른다. 만료·위조 토큰은 항상 401.
- 크롤링 실패는 기존 데이터를 건드리지 않고 로그만 남긴다 (F2-1).
- 기능 테스트에는 `@pytest.mark.feature("F?-?")`로 기능 ID를 단다.
