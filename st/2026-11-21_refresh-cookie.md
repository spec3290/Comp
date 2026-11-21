# 코드 리뷰: Refresh Token HttpOnly 쿠키 저장 (#74, F1-4·F1-5·F1-6, Q-41)

범위: `git diff origin/develop` + 새 파일 `tests/unit/test_refresh_cookie.py`, `st/2026-11-21_refresh-cookie.md`.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- **프론트 호환이 깨지는 변경이다.** 응답 본문의 `refresh_token`이 사라지고 `/auth/refresh`는 본문을 읽지 않는다. 프론트는 `/auth/token`·`/auth/refresh`·`/auth/logout`을 `credentials: "include"`로 불러야 한다. 이게 없으면 교차 출처(localhost:5173 → :8000) 응답의 Set-Cookie를 브라우저가 버린다. 프론트와 같이 배포한다.
- 쿠키 Path가 `/auth`로 고정이다. 운영에서 리버스 프록시가 API를 `/api/auth/...`처럼 접두사 아래로 내보내면 쿠키가 실리지 않는다. 배포 결정(Q-37) 때 프록시 경로를 확인한다.
- `Origin`은 `FRONTEND_LOGIN_REDIRECT_URL`의 출처와 정확히 같아야 한다. 로컬에서 프론트를 `127.0.0.1:5173`으로 열면 403 `ORIGIN_FORBIDDEN`이다. 기존 CORS 허용 출처와 같은 기준이라 그대로 뒀다.
- 재발급이 401(만료·회전·폐기)로 실패해도 브라우저에 죽은 쿠키가 남는다. 에러 응답은 공통 핸들러가 만들어 라우터에서 지우기 어렵고, 다음 로그인에서 덮어쓰므로 해가 없어 두었다.
- 로그아웃은 Access가 필요하다. Access가 만료되면 401이라 쿠키를 지우지 못한다. 프론트는 만료 시 재발급한 뒤 로그아웃한다(기존 흐름과 같다).

### 확인한 것
- 명세: F1-4(회전·401), F1-5(Refresh 삭제), F1-6(로그인 발급) 수용 기준은 그대로이고 전달 방식만 바꿨다. features.md에 진행 메모를 적었다. 상태(F1-4·F1-5 done, F1-6 wip)는 그대로다. MVP 밖 기능은 없다.
- 쓰임새: 새로 저장하는 데이터 없음. 쿠키는 기존 Refresh 값을 담는 통로일 뿐이고 Redis 저장 방식(ADR-0002)은 바뀌지 않았다.
- 결정: Q-41을 사용자 결정(쿠키만, SameSite=Lax, Origin 검사)으로 추가했다. ADR-0004의 재발급·소셜 로그인·에러 코드(403 `ORIGIN_FORBIDDEN`)와 엔드포인트 표를 고쳤다. 에러는 `AppError` 단일 포맷이다. Swagger 토큰 응답도 본문에서 Refresh를 뺐다. 재발급이 쿠키로만 받으니 쓸 데가 없어서이고, 질문 때 안내한 "Swagger는 본문 유지"와 다른 점을 Q-41에 적었다.
- 아키텍처: Origin 검사와 Secure 판단은 `AuthService`에 있다. 라우터는 쿠키·헤더를 읽고 쿠키를 심는 일만 한다. repository를 import하지 않는다(AC-2). 새 `noqa`·`type: ignore`는 없다.
- 보안: Refresh는 HttpOnly라 JS(XSS)로 읽을 수 없다. SameSite=Lax라 교차 사이트 POST에는 쿠키가 실리지 않고, Origin을 정확히 비교해 같은 사이트의 다른 서브도메인·포트·scheme도 403이다. Origin이 없으면 거절한다. 거절할 때는 Refresh를 회전하지 않는다(테스트로 확인). Secure는 https 프론트일 때 붙는다. CORS credentials는 프론트 출처 하나에만 허용하고 와일드카드가 없다. 로그에 토큰이 남지 않는다.
- 주석: 새로 쓰거나 고친 함수·클래스·테스트에 한 줄 docstring과 근거(Q-41)를 적었다.
- 테스트: `tests/unit/test_refresh_cookie.py` 11개를 추가했다. 쿠키 속성(HttpOnly·Lax·Path·Max-Age·Secure 없음/있음), 본문에 Refresh 없음, 재발급 회전과 응답 필드, 쿠키만으로 연속 재발급, Origin 없음·다른 호스트·포트·scheme 403과 미회전, 본문 토큰 무시, 로그아웃 쿠키 삭제, CORS credentials를 본다. 기존 재발급·로그아웃·탈퇴 테스트는 쿠키+Origin 헬퍼(`auth_support.refresh`)로 옮겼다. "본문 없으면 400" 테스트는 "쿠키 없으면 401"로 바꿨다. 모두 `@pytest.mark.feature`가 달려 있고 실제 네트워크 호출은 없다.
- Git 규약: 이슈 #74, 브랜치 `feature/74_refresh-cookie`.

## 실행한 검사

| 명령 | 결과 |
|---|---|
| `uv run ruff format --check .` | 110 files already formatted |
| `uv run ruff check .` | All checks passed! |
| `uv run mypy` | Success: no issues found in 75 source files |
| `uv run lint-imports` | Contracts: 3 kept, 0 broken |
| `uv run pytest` | 362 passed, 36 skipped (MONGODB_TEST_URI 미설정 등) |
