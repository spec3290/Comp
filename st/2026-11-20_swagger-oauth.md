# 코드 리뷰: Swagger Authorize 로 소셜 로그인 테스트 (#72, F1-6, Q-40)

범위: `git diff origin/develop` + 새 파일 `tests/unit/test_swagger_oauth.py`, `st/2026-11-20_swagger-oauth.md`.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- 실제 브라우저에서 Google 계정으로 Swagger Authorize 팝업 → `/docs/oauth2-redirect` → tokenUrl 흐름은 아직 손으로 확인하지 않았다. 테스트는 FakeProvider 와 OpenAPI 스키마로 검증했다. 테스트 서버(EC2)에 `SWAGGER_OAUTH_ENABLED=true` 로 올린 뒤 한 번 확인한다.
- `/docs` 자체는 운영에서도 열려 있다(이번 변경 전부터). 운영에서 문서를 숨길지는 배포 결정(Q-37)과 같이 정한다.
- Swagger 콜백도 로그인 코드를 URL 쿼리로 받는다. 프론트 흐름(Q-16)과 같은 수준이고 60초·일회용이라 허용했다.

### 확인한 것
- 명세: F1-6 진행 메모에 Q-40 을 적었다. 상태(wip)는 Kakao 가 남아 그대로다. MVP 밖 기능은 없다(테스트 도구).
- 쓰임새: 새로 저장하는 데이터 없음. `oauth_swagger_state` 쿠키는 콜백에서 Swagger 로 돌려보낼 때만 읽고 성공·실패 모두 지운다.
- 결정: Q-40 을 사용자 결정으로 추가하고 ADR-0004 소셜 로그인 항목에 엔드포인트·쿠키·에러 코드(`SWAGGER_OAUTH_INVALID`, 400)를 적었다. 에러는 ADR-0004 단일 포맷(`AppError`)이다.
- 아키텍처: 검증·목적지 결정은 `AuthService`, 라우터는 쿠키·폼 변환과 OpenAPI 보정(`add_swagger_oauth`)만 한다. repository 직접 import 없음(AC-2), core 는 기능 패키지를 import 하지 않는다(AC-3, 설정 필드만 추가). 새 `# noqa: S105`(엔드포인트 경로 상수, 기존 `TOKEN_URL` 과 같은 이유)와 `# type: ignore[method-assign]`(FastAPI 문서의 `app.openapi` 교체 방식)은 타당하다.
- 보안: 기본 꺼짐. redirect_uri 는 `GOOGLE_REDIRECT_URI` 출처 + `/docs/oauth2-redirect` 와 정확히 같아야 한다(Host 헤더를 믿지 않아 오픈 리다이렉트·코드 유출을 막는다). Swagger state 는 base64 문자 256자 이하만 받고, 콜백에서도 다시 검사한다. 프론트 로그인 시작은 남은 Swagger 쿠키를 지운다. 토큰 검증 경로(`require_access`)는 바뀌지 않았다. 로그에 코드·토큰을 남기지 않는다.
- 주석: 새로 쓰거나 고친 함수·클래스·테스트에 한 줄 docstring, 규칙 근거(Q-40)를 적었다.
- 테스트: `tests/unit/test_swagger_oauth.py` 17개, 모두 `@pytest.mark.feature("F1-6")`. 성공 흐름, 받은 토큰으로 `/users/me`, 실패 시 Swagger 로 error·state, 꺼짐 400(login·token), 다른 출처·경로·잘못된/빈 state 400, 버려진 Swagger 쿠키 뒤 프론트 로그인, 다른 grant_type 400, 코드 재사용 401, 콜백 출처 계산, OpenAPI 스킴 추가·무변경. 실제 네트워크 호출 없음.
- Git 규약: 이슈 #72, 브랜치 `feature/72_swagger-oauth`.

## 실행한 검사

| 명령 | 결과 |
|---|---|
| `uv run ruff format --check .` | 108 files already formatted |
| `uv run ruff check .` | All checks passed! |
| `uv run mypy` | Success: no issues found in 74 source files |
| `uv run lint-imports` | Contracts: 3 kept, 0 broken |
| `uv run pytest` | 351 passed, 36 skipped (MONGODB_TEST_URI 미설정 등) |
| `SWAGGER_OAUTH_ENABLED=true ... create_app()` 의 `/openapi.json` | `googleOAuth2` 스킴(authorizationUrl `/auth/google/login`, tokenUrl `/auth/swagger/token`), `/users/me` security 에 `HTTPBearer` 와 `googleOAuth2`, `/docs` 에 `clientId` 초기값 |

## CI (PR #73)

| 검사 | 결과 |
|---|---|
| lint / type / architecture / test | pass |
| docker image | pass |
| git conventions | pass |
| CodeRabbit | pass (지적 없음) |
