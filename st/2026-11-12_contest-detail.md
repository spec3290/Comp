# 코드 리뷰: 대회 상세 보강

- 날짜: 2026-11-12
- 브랜치: `feature/46_contest-detail` (이슈 #46)
- 기능: F2-4(대회 상세)
- 결정: Q-29 신설(상세에 더할 값, 사용자 결정), ADR-0004 대회 응답 필드 갱신

## 배경
목록은 대회를 훑어보는 용도이고 상세는 마음에 든 대회를 자세히 보는 용도인데, 기존 상세 응답은 목록 항목에 출처 이름 목록(`sources: ["dacon", ...]`)만 더한 수준이었다. MongoDB `contests` 문서에서 목록이 쓰지 않는 값(`sources[]`의 사이트별 링크·수집 시각, 대표 `source`, `created_at`, `updated_at`, `merge_key`)을 정리해 사용자와 함께 골랐다.

- 넣음: `sources` → `[{ source, source_url, is_representative }]`(사이트별 원문 링크), `updated_at`(대표 소스가 마지막으로 값을 쓴 시각), `bookmarked`(로그인 회원의 저장 여부, 비로그인은 null)
- 뺌: `created_at`(사용자 결정), `merge_key`·사이트별 `fetched_at`(내부 값)
- 이번 범위 밖: 크롤러가 읽고 버리는 진행 방식(온·오프라인)·장소 저장(사용자 결정)
- 유지: 마감 대회 상세는 삭제(마감 3일 뒤, Q-3) 전까지 열린다(사용자 결정)

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
1. **`sources` 응답 형태가 바뀐다 (호환성 깨짐)** — 문자열 목록에서 객체 목록으로 바뀌었다. 상세의 `sources`를 쓰던 프론트가 있으면 함께 고쳐야 한다. 목록 응답은 바뀌지 않았다.
2. **`updated_at`의 의미** — 수집 때마다 대표 소스가 값을 다시 쓰므로 "값이 바뀐 시각"이 아니라 "대표 소스에서 마지막으로 확인한 시각"에 가깝다. 대표가 아닌 소스의 병합은 이 값을 바꾸지 않는다(`app/contests/repository.py`의 `save`). 화면 문구는 "정보 확인: MM/DD"처럼 쓰는 편이 정확하다.
3. **비로그인 상세도 PostgreSQL 세션을 연다** — `get_bookmark_service`가 세션을 주입하지만, 비로그인이면 `is_bookmarked`가 쿼리 없이 None을 돌려준다. SQLAlchemy 세션은 첫 쿼리 때 접속하므로 실제 연결은 생기지 않는다. 목록도 같은 이유로 `get_user_service`를 이미 주입하고 있다.

### 확인한 것
- 명세: F2-4 수용 기준(정보, D-day, 분야·형식 태그)에 Q-29 항목을 더해 구현, 상태 `wip` → `done`. 참가 자격 원문은 계속 없음(Q-23).
- 결정: 열린 Q-항목을 추측으로 구현하지 않았다. Q-29를 사용자 답으로 `결정됨`으로 남기고 ADR-0004 대회 응답 필드를 같은 변경에서 고쳤다. 접수 상태·D-day는 계속 조회 시 계산한다(ADR-0005).
- 아키텍처: 라우터는 `ContestQueryService.get_contest`와 `BookmarkService.is_bookmarked`만 부르고 repository를 직접 import하지 않는다(AC-2). 대표 여부 판단은 문서 필드 비교라 repository 변환(`_to_record`)에 두었다. 상세 생성자는 `ContestSummaryOut.from_view`와 시그니처가 달라 mypy 오버라이드 오류가 나서 `from_detail`로 이름을 나눴다. AC-1~3 유지, `# noqa`·`# type: ignore` 추가 없음.
- 보안: 저장 여부는 토큰으로 검증한 본인 것만 조회한다(다른 회원 저장은 섞이지 않음 테스트). 만료·위조 토큰은 기존처럼 401(`OptionalUserId`). 응답에 내부 키 없음.
- 주석: 새로 쓰거나 고친 클래스·함수(`ContestOrigin`, `ContestSourceOut`, `from_detail`, `is_bookmarked`, `exists`, 테스트) 모두 한 줄 docstring.
- 테스트: `test_contest_api.py` — 사이트별 링크·대표 여부·`updated_at`(KST ISO 8601)·내부 값 미노출, 비로그인 `bookmarked` null, 로그인 회원의 저장/미저장 구분, 목록에는 상세 전용 필드 없음. `test_contest_repository.py` — 두 소스 병합 후 `get`이 대표 여부와 대표 소스의 `updated_at`을 돌려줌(MongoDB 통합, 로컬은 `MONGODB_TEST_URI`가 없어 스킵, CI의 mongo 서비스에서 실행). 네트워크 호출 없음.
- Git 규약: 이슈 #46, `feature/46_contest-detail`, 커밋 `⚡`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` | 통과 (79 files already formatted) |
| `uv run ruff check .` | 통과 |
| `uv run mypy` | 통과 (63 source files) |
| `uv run lint-imports` | 통과 (3 kept, 0 broken) |
| `uv run pytest` | 282 passed, 17 skipped (MongoDB 통합 테스트, `MONGODB_TEST_URI` 미설정) |
