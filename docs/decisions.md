# 결정 내역

확정된 결정(ADR)과 아직 결정이 필요한 항목을 이 파일 하나에서 관리한다. 이전의 `decisions/` 폴더, `open-questions.md`, 아키텍처·데이터 모델·API 규약 문서는 내용을 줄여 여기로 통합했다.

## 운영 규칙

- 새 결정은 `ADR-NNNN` 번호를 이어서 붙여 `결정 목록`에 추가한다. 항목은 `배경` / `결정` / `대안` / `결과` 네 줄이면 충분하다.
- 상태는 `제안` → `채택` → (`폐기` / `대체됨`). 채택된 결정을 뒤집을 때는 지우지 않고 상태를 바꾸고 새 번호로 이유를 쓴다.
- 구조 제약(AGENTS.md의 AC 규칙)을 완화하는 결정은 사용자 승인을 받은 뒤 `pyproject.toml`과 AGENTS.md를 **같은 변경**에서 고친다.
- 명세가 비어 있어 추측이 필요하면 구현하지 말고 `결정이 필요한 항목` 표에 Q-번호로 추가하고 사용자에게 묻는다. 결정되면 ADR이나 features.md에 반영하고 상태를 `결정됨`으로 바꾼다.

## 결정 목록

| ID | 제목 | 상태 | 관련 기능 |
|---|---|---|---|
| ADR-0001 | 레이어드 아키텍처와 import-linter | 대체됨 (ADR-0007) | 전체 |
| ADR-0002 | JWT와 Refresh Token 회전 | 채택 | F1-4, F1-5 |
| ADR-0003 | 소셜 로그인만 지원 | 채택 | F1-6, F1-8 |
| ADR-0004 | API 규약 (에러·인증·페이지네이션) | 채택 | 전체 |
| ADR-0005 | 데이터 모델 원칙과 초안 | 제안 | 전체 |
| ADR-0006 | 검증은 GitHub Actions CI로 일원화 | 채택 | 전체 |
| ADR-0007 | 기능별 패키지 + 얇은 3계층으로 단순화 | 채택 | 전체 |
| ADR-0008 | PostgreSQL(메인) + MongoDB(대회·벡터) 이원 저장소, Refresh Token은 Redis | 채택 | 전체 (ADR-0005) |

### ADR-0001 레이어드 아키텍처와 import-linter (대체됨 → ADR-0007)
- **배경**: 에이전트가 빠르게 코드를 만들수록 구조가 흐트러진다. 문서만으로는 레이어 규칙이 지켜지지 않는다.
- **결정**: `api/jobs → services → repositories → domain`, 외부 시스템은 `domain/ports.py`의 Protocol 뒤에 둔다. 규칙은 import-linter 계약(AC-1~AC-6, `pyproject.toml`)으로 표현하고 CI가 검사한다.
- **대안**: 기능별 수직 슬라이스(MVP 규모에서 이득이 작고 수집·인증은 기능 간 공유가 많음), 문서로만 관리(위반이 누적됨).
- **결과**: 위반이 PR에서 바로 보이고 도메인·크롤러·OAuth를 가짜로 테스트한다. ports·DTO 변환 코드가 늘어난다.

### ADR-0002 JWT와 Refresh Token 회전 (채택)
- **배경**: F1-4/F1-5가 서비스 자체 JWT, Refresh 회전, 만료·위조 401, 로그아웃 시 Access 블랙리스트를 요구한다.
- **결정**: Access는 짧은 만료(15분, `sub`/`jti`/`fam`/`type`/`iat`/`exp`, HS256). `fam`은 로그인 세션(family) 식별자로 Refresh와 같다. Refresh는 14일, JWT(`sub`/`jti`/`fam`/`type`/`iat`/`exp`)로 발급하고 Redis의 `refresh:{jti}` 해시(`user_id`, `family_id`)에 만료와 같은 TTL로 저장하며(Q-4), 재발급마다 회전한다. 회전한 토큰은 지우지 않고 `rotated` 표시만 남겨 만료까지 재사용을 탐지하고, 표시는 WATCH 트랜잭션으로 해서 같은 토큰의 동시 재발급도 한 번만 성공한다. family의 jti는 `refresh_family:{family_id}` 집합에 모은다. 이미 회전된 Refresh가 다시 오면 탈취로 보고 같은 `family_id`를 전부 폐기하고 401 (다른 기기의 로그인 family는 유지). 로그아웃은 Access의 `fam`으로 그 세션의 Refresh를 전부 지우고(다른 기기 로그인은 유지), 쓰던 Access `jti`를 Redis `blacklist:{jti}`에 남은 만료 시간만큼 TTL로 둔다(Q-17). 인증이 필요한 요청마다 블랙리스트를 조회한다.
- **대안**: 세션 쿠키(명세가 JWT를 명시), Refresh 비회전(탈취 탐지가 어려움).
- **결과**: 탈취 탐지와 즉시 로그아웃을 얻는다. 요청마다 블랙리스트 조회 비용과 정리 작업이 생긴다. Refresh 저장소가 Redis라 Redis가 내려가면 로그인·재발급이 실패한다.

### ADR-0003 소셜 로그인만 지원
- **배경**: 명세가 회원가입(F1-1)·ID/비밀번호 로그인(F1-3)을 제외로 분류하고 소셜 로그인(F1-6)을 유일한 수단으로 정했다.
- **결정**: Google/Kakao OAuth2만 지원하고 최초 로그인 시 `provider + providerId`로 자동 가입한다. 비밀번호 저장·재설정·가입 폼은 만들지 않는다. 탈퇴(F1-8) 시 OAuth 연결을 해제한다.
- **대안**: ID·비밀번호 병행(비밀번호 보관·재설정 책임이 생기고 명세가 제외).
- **결과**: 비밀번호 보안 부담이 없고 가입이 짧다. 제공자 장애·정책 변경에 종속되고, 이메일 없는 계정의 알림 채널(2차)은 별도 확인이 필요하다.

### ADR-0004 API 규약
- **배경**: 엔드포인트가 늘기 전에 에러·인증·페이지네이션 형태를 하나로 고정한다.
- **결정**:
  - 인증은 `Authorization: Bearer <access_token>`. 만료·위조·블랙리스트·이미 회전된 Refresh는 모두 **401**이고 `error.code`로 `TOKEN_EXPIRED` / `TOKEN_INVALID`를 구분한다. 헤더가 없거나 Bearer가 아니면 401 `TOKEN_INVALID`. Access 자리에 Refresh를 내거나 그 반대도 `TOKEN_INVALID`(`type` 클레임).
  - 재발급(F1-4): `POST /auth/refresh` 본문 `{ "refresh_token": "..." }` → `{ access_token, refresh_token, token_type, expires_in }`. 탈퇴한 회원의 Refresh는 `TOKEN_INVALID`.
  - 에러 본문은 `{ "error": { "code": "CONTEST_CLOSED", "message": "..." } }` 하나. 400 `VALIDATION_ERROR`, 401 토큰 오류, 404 `NOT_FOUND`, 409 상태 충돌(`CONTEST_CLOSED` 등), 500은 상세를 로그에만 남긴다.
  - 소셜 로그인(F1-6): `login`은 제공자 동의 화면으로 302 리다이렉트하고 state를 `oauth_state` 쿠키(HttpOnly, SameSite=Lax, Path=/auth, 10분)에 담는다. `callback`은 성공 시 `{ access_token, refresh_token, token_type: "bearer", expires_in, needs_onboarding }` JSON(Q-16). state 불일치·누락은 400 `OAUTH_STATE_MISMATCH`, 제공자가 코드를 거절하거나 사용자가 취소하면 401 `OAUTH_FAILED`, 제공자 장애나 형식이 깨진 응답은 502 `OAUTH_UNAVAILABLE`, 지원하지 않거나 클라이언트 ID·시크릿이 설정되지 않은 제공자는 404 `NOT_FOUND`. state 쿠키의 `Secure`는 요청 scheme이 아니라 콜백 주소(`GOOGLE_REDIRECT_URI`)가 https인지로 정한다(TLS를 끝내는 프록시 뒤에서도 유지).
  - 페이지네이션은 `?page=1&size=20`(size 상한 100), 응답 `{ items, page, size, total }`.
  - 목록 쿼리: `sort=deadline|latest`, `status=open,upcoming,closed`(기본 open,upcoming), `field`, `q`, `education`, `team_type`, `region`. 같은 파라미터 안은 OR, 파라미터 사이는 AND(Q-9). `미분류`는 제외하지 않고 `eligibility_check_needed: true`로 표시.
    - 현재 구현(F2-3): `sort`, `status`, `field`(분야 텍스트 부분 일치, 대소문자 무시), `q`(제목 부분 일치, 최대 100자). 기본 `size=20`. 자격 파라미터(`education` 등)와 `eligibility_check_needed`는 F3-1·F3-3에서 추가.
  - 대회 응답 필드: `id`, `title`, `host`, `field`, `status`(`open`/`upcoming`/`closed`, 조회 시 계산), `d_day`(마감 당일 0), `apply_start`, `apply_end`, `event_start`, `event_end`(날짜 `YYYY-MM-DD`, 없으면 null), `apply_url`, `source_url`. 상세는 `eligibility_text`, `sources`(수집 출처 목록)를 더한다.
- **엔드포인트 초안**:

| 기능 | 메서드·경로 | 인증 |
|---|---|---|
| F1-6 | `GET /auth/{provider}/login`, `GET /auth/{provider}/callback` (`provider`: google/kakao) | 없음 |
| F1-4 | `POST /auth/refresh` (본문 `refresh_token`) | Refresh |
| F1-5 | `POST /auth/logout` → 204 | Access |
| F1-7 | `GET /users/me`, `PUT /users/me/profile` | Access |
| F1-8 | `DELETE /users/me` | Access |
| F2-3 | `GET /contests` | 선택 (로그인 시 프로필이 자격 필터 기본값) |
| F2-4 | `GET /contests/{id}` | 선택 |
| F2-5 | `POST /contests/{id}/clicks` → `{ "apply_url": ... }` | 선택 |
| F2-7 | `PUT`·`DELETE /contests/{id}/bookmark`, `GET /users/me/bookmarks` | Access |

- **결과**: 클라이언트가 한 가지 에러 처리만 구현하면 된다. 규약을 바꿀 때는 이 항목을 고친다.

### ADR-0005 데이터 모델 원칙과 초안 (제안)
- **원칙**: 시각은 tz-aware(UTC 저장, KST 변환). **접수 상태·D-day는 저장하지 않고** 조회 시점의 KST 날짜로 계산한다. 키워드 규칙·태그 어휘는 데이터/enum 하나를 단일 출처로 하고 프로필·태그·필터가 같이 쓴다. 마이그레이션은 Alembic.
- **초안**:

| 테이블 | 핵심 컬럼·제약 |
|---|---|
| users | id, provider, provider_id, nickname, grade, school_type, created_at. `UNIQUE(provider, provider_id)`, 프로필 미설정이면 nickname NULL |
| user_interest_fields | user_id, field |
| refresh_tokens (Redis) | 테이블 없이 `refresh:{jti}` 해시: user_id, family_id. 만료는 Redis TTL (Q-4) |
| access_token_blacklist (Redis) | 테이블 없이 `blacklist:{jti}`. 만료는 Redis TTL이라 정리 작업이 없다 (Q-17) |
| contests (MongoDB) | _id, merge_key(`UNIQUE`, 정규화한 제목+주최+마감일), title, host, field, apply_start, apply_end, event_start, event_end, eligibility_text, apply_url, source_url, source(대표 소스), sources[], created_at, updated_at. 날짜는 KST 자정 tz-aware datetime으로 저장(BSON에 날짜 타입이 없음) |
| contest_sources (MongoDB) | 별도 컬렉션 없이 `contests.sources[]`에 내장: source, source_url, fetched_at (병합 근거) |
| contest_tags (MongoDB) | contest_id, dimension(학력/참가형태/분야/지역), value, origin(rule/llm). 태그가 없으면 미분류 |
| qualification_rules | id, dimension, keyword, value, enabled |
| contest_clicks | id, contest_id, user_id NULL, clicked_at (클릭 수는 집계) |
| crawl_runs | id, source, started_at, finished_at, status, error |
| bookmarks | `PK(user_id, contest_id)`, created_at |

- **인덱스 메모**: `contests(apply_end)`, `contest_tags(dimension, value)`, 제목 검색은 MongoDB 쿼리로 한다(방식은 구현 시 결정). 표에 `(MongoDB)`·`(Redis)`가 없는 테이블은 PostgreSQL이다(ADR-0008). users는 Alembic 마이그레이션 `0001`로 만들었다. 탈퇴(F1-8)는 users와 참조 행을 모두 삭제한다. 접수 마감 3일이 지난 대회는 수집 실행 때 하드 삭제하므로(Q-3) 이를 참조하는 bookmarks·contest_clicks는 대회가 없을 수 있음을 전제로 한다. 같은 소스·원문 URL의 대회가 마감일·제목을 바꾸면 merge_key를 옮겨 같은 문서를 유지한다.

### ADR-0006 검증은 GitHub Actions CI로 일원화
- **배경**: 로컬 훅·Makefile·문서 검사 스크립트가 늘면서 하네스가 잘게 쪼개졌다.
- **결정**: 린트·타입·아키텍처·테스트·Git 규약 검사는 `.github/workflows/ci.yml` 하나가 담당한다. 로컬 훅, Makefile, pre-commit, 문서 정합성 스크립트는 두지 않는다. 에이전트는 PR 전에 같은 명령을 로컬에서 돌려 볼 수 있지만 통과 기준은 CI다.
- **대안**: 로컬 훅 + CI 이중 검증(관리할 파일이 많고 규칙이 두 곳에서 어긋남).
- **결과**: 규칙의 단일 출처가 CI가 된다. 위반을 PR을 올린 뒤에야 알게 되므로, 로컬 사전 점검 명령을 AGENTS.md에 적어 둔다.

### ADR-0007 기능별 패키지 + 얇은 3계층으로 단순화
- **배경**: ADR-0001의 전역 레이어 + ports 구조는 개인 프로젝트 MVP 규모에서 기능 하나가 4~5개 레이어에 흩어지고, 구현체가 하나뿐인 Protocol(`Clock` 등)이 늘어 이득보다 비용이 컸다.
- **결정**: 기능 단위 패키지(`app/contests`, `app/auth`) 안에 `router` → `service` → `repository`를 두고, 순수 도메인 규칙은 `rules.py`, 공통 설정·시간 등은 `app/core`에 둔다. Protocol은 구현체가 여러 개인 경계(크롤러 `ContestSource`, OAuth `OAuthProvider`)에만 쓴다. import-linter 계약은 AC-1~AC-3 세 개로 줄인다.
- **대안**: ADR-0001 유지(구조 강제는 강하지만 MVP 속도가 느림), 계약 없이 문서로만 관리(위반이 누적됨).
- **결과**: 기능 코드가 한 폴더에 모여 찾기·수정이 쉽다. 서비스가 SQLAlchemy 등을 직접 알 수 있어 테스트는 DB 픽스처에 더 기댄다. 규모가 커지면 ports·계약을 다시 늘리는 ADR을 쓴다.

### ADR-0008 PostgreSQL(메인) + MongoDB(대회·벡터) 이원 저장소
- **배경**: 크롤링한 대회 데이터는 소스마다 형태가 달라 스키마가 자주 바뀌고, 이후 대회 임베딩을 저장·검색할 벡터 저장소가 필요하다. 사용자·토큰 등은 관계형 제약(UNIQUE, 트랜잭션)이 중요하다.
- **결정**: PostgreSQL(SQLAlchemy + Alembic, `migrations/`)을 메인 DB로 두고 users, bookmarks, contest_clicks, crawl_runs, qualification_rules 등을 저장한다. MongoDB(pymongo)에는 대회 데이터(contests, contest_sources, contest_tags)와 벡터(임베딩)를 저장한다. 만료가 있는 Refresh Token과 Access 블랙리스트는 Redis(redis-py)에 둔다(Q-4, Q-17). 연결은 `app/core/db.py`(MongoDB), `app/core/sql.py`(PostgreSQL), `app/core/kv.py`(Redis)에 두고, 실제 접근은 각 기능의 `repository`에서만 한다. 로컬 개발용 PostgreSQL·Redis는 `docker-compose.yml`로 띄운다.
- **대안**: PostgreSQL 단일(+pgvector, 저장소 하나로 단순하지만 사용자가 MongoDB를 선택), MongoDB 단일(인증·토큰 회전의 관계형 제약을 직접 구현해야 함).
- **결과**: 대회 스키마 변경에 마이그레이션이 필요 없고 벡터를 대회 문서 옆에 둘 수 있다. 두 DB 사이에는 FK·트랜잭션이 없어서 bookmarks·contest_clicks의 `contest_id`는 MongoDB `_id` 문자열을 참조만 하고, 대회 삭제·병합 시 정합성은 서비스 코드가 맞춘다.

## 결정이 필요한 항목

| ID | 질문 | 영향 기능 | 제안(기본값) | 상태 |
|---|---|---|---|---|
| Q-1 | 크롤링 대상 사이트(소스) 목록과 robots.txt/이용약관 허용 범위는? | F2-1 | dev-event.vercel.app, dacon.io, linkareer.com (사용자 지정). 세 곳 모두 robots.txt가 목록 경로를 허용(2026-10-23 확인). 소스별 어댑터 + 링커리어 페이지 간 1초 지연. 이용약관은 미확인 | 결정됨 |
| Q-2 | 중복 제거에서 제목 정규화(공백·특수문자·대소문자·접두어) 수준과 대표 값 선택 규칙은? | F2-2 | 공백·특수문자 제거 후 소문자 비교, 먼저 수집된 소스를 대표로. 구현은 이 기본값 + 맨 앞 `[...]` 접두어 제거로 동작 중 | 열림 |
| Q-3 | 마감 대회를 상태 필터 '마감'으로 조회할 수 있어야 하나? | F2-3 | 기본 목록에서 제외하고 `status=closed`로 조회 가능. 접수 마감 3일이 지나면(마감일 < 오늘-3일) 수집 실행 때 하드 삭제한다 | 결정됨 |
| Q-4 | Access/Refresh 만료 시간과 Refresh Token 저장소는? | F1-4, F1-5 (ADR-0002) | Access 15분, Refresh 14일. Refresh Token은 Redis에 저장 (사용자 지정). 블랙리스트 저장소는 Q-17로 분리 | 결정됨 |
| Q-5 | 닉네임 길이·중복 허용 여부, 학년·학교 유형의 허용 값은? | F1-7 | 2~12자, 중복 불가. 학교 유형: 중/고/대학교/기타 | 열림 |
| Q-6 | 접수 시작일이 없는 대회의 상태 판정은? | F2-3 | 소스에 값이 있으면 크롤링한다(DACON은 일정 HTML의 '참가(신청) 기간', 없으면 대회 기간). 그래도 시작일이 없으면 '접수 중'으로 간주 | 결정됨 |
| Q-7 | 회원 탈퇴 시 OAuth revoke가 실패하면 탈퇴를 진행하나? | F1-8 | 로컬 삭제는 진행, revoke 실패는 로그 + 재시도 큐는 2차 | 열림 |
| Q-8 | 참가 형태·지역 키워드 목록의 초기 규칙 세트는? | F3-1 | 사용자가 초안 제공, 에이전트가 설정 파일로 구성 | 열림 |
| Q-9 | 한 대회에 여러 태그가 걸릴 때(예: 고등학생+대학생) 필터 매칭은 OR인가 AND인가? | F2-3, F3-3 (ADR-0004) | 같은 파라미터 안은 OR, 파라미터 사이는 AND | 결정됨 |
| Q-10 | 대회 일정이 여러 구간이면 어떻게 저장하나? | F2-1, F4 | MVP는 시작일·종료일 한 쌍만 저장 | 열림 |
| Q-11 | 커밋 메시지의 `(#이슈번호)`는 PDF 규칙(merge/PR에만 사용)과 예시(일반 커밋에도 사용)가 어긋난다. 어느 쪽이 맞나? | git-workflow 스킬 | merge/PR(🔀)에는 필수, 일반 커밋에는 선택 | 열림 |
| Q-12 | PDF의 '기능 삭제' 커밋 타입 이모지는 이미지상 관(⚰️)으로 보이는데 맞나? | git-workflow 스킬 | ⚰️ 로 가정 | 열림 |
| Q-13 | 저장소를 MongoDB(pymongo)로 바꾸나, PostgreSQL(SQLAlchemy)과 함께 쓰나? AGENTS.md 스택·ADR-0005는 PostgreSQL+Alembic 기준이다 | 전체 (ADR-0005) | PostgreSQL 메인 + MongoDB(대회 데이터·벡터) → ADR-0008 | 결정됨 |
| Q-14 | Dev Event에서 접수 기간 없이 행사 당일 일정(`DATE`)만 있는 대회의 접수 마감일은? | F2-1, F2-3 | 행사 시작일을 접수 마감일로 간주 | 결정됨 |
| Q-15 | 링커리어 공모전은 분야 제한 없이(네이밍·사진·UCC 등 포함) 모두 수집하나, 개발·AI·보안·디자인·아이디어 등 특정 카테고리만 수집하나? | F2-1, F3-1 | 과학/공학(35)·기획/아이디어(28) 카테고리만 수집. 링커리어에 IT/소프트웨어 카테고리가 따로 없어 개발 대회는 주로 이 둘에 있음. 창업·학술·디자인·게임 등은 제외 | 결정됨 |
| Q-16 | 소셜 로그인 콜백이 서비스 JWT를 클라이언트에 어떻게 전달하나? (JSON 응답 / 프론트로 리다이렉트 / 프론트가 code를 POST) | F1-6 (ADR-0004) | 콜백이 JSON으로 응답. 프론트엔드가 생기면 리다이렉트 방식을 다시 검토 | 결정됨 |
| Q-17 | 로그아웃 시 Access Token 블랙리스트 저장소는? | F1-5 (ADR-0002) | Refresh와 같은 Redis에 `blacklist:{jti}`를 Access 남은 만료만큼 TTL로 저장 (사용자 결정) | 결정됨 |
