# 코드 리뷰: 접수 마감일을 못 찾으면 null로 저장

- 날짜: 2026-11-13
- 브랜치: `feature/56_unknown-deadline` (이슈 #56)
- 기능: F2-1(수집), F2-2(중복 제거), F2-3(목록), F2-4(상세)
- 결정: Q-35 신설(결정됨, 사용자 결정). Q-14는 Q-35로 대체, Q-2·Q-26·Q-27 문구 수정

## 배경
사용자 결정: 크롤러가 접수 마감일을 못 찾으면 이상한 대체 값을 넣지 말고 null로 저장하고, 화면은 '접수 마감일 미확인'으로 표시한다. 경진대회는 대회 기간 중이면 참가할 수 있으므로 대회 마감일을 접수 마감일로 쓴다.

지금까지의 대체 값:
- Dev Event `DATE` 일정: 행사 시작일을 마감일로 넣었다(Q-14).
- 이벤터스 `register_due_date` 없음: 행사 시작일을 넣었다.
- 코드포코리아 일시 '미정': 대회를 건너뛰었다.

실데이터 117건 중 대체 마감일이 들어간 출처는 dev_event 3건이었다(읽기 전용으로 확인). 그중 경진대회는 없었다.

## 바뀐 것
- 크롤러: `RawContest.apply_end`를 `date | None`으로 바꿨다. 위의 세 곳은 이제 null을 넣는다. Dev Event `DATE` 일정은 대회 기간(`event_start`~`event_end`)으로 옮겼다.
- `rules.py`:
  - `ApplyPeriod.end`를 nullable로 바꿨다.
  - `closes_on`(참가 마지막 날: 마감일 → 대회 끝나는 날 → 대회 시작일 → 날짜가 없으면 `OPEN_ENDED`)을 추가했다.
  - 마감일을 모르면 `d_day`는 None이다.
  - `fill_competition_deadline`: 마감일 없는 경진대회는 `event_end`로 채운다.
  - `same_contest`: null 마감일끼리는 같은 마감일로 보지 않는다.
  - `resolve`: 접수 기간은 마감일을 찾은 출처 중 앞선 출처의 값을 쓴다.
- `service.py`: 분류한 뒤 경진대회 마감일을 채운다. 삭제 기준을 넘겼는지는 `closes_on`으로 판단한다.
- `repository.py`:
  - 최상위 `closes_on`을 저장한다. 상태 필터·마감 임박순·삭제가 이 값을 쓴다.
  - 이전 문서는 `ensure_indexes`가 `closes_on`을 채운다(멱등).
  - `apply_end_1` 인덱스를 `closes_on_1`로 바꿨다.
  - 마감일이 null이면 후보 검색에서 마감일 조건을 뺀다.
- API: `apply_end`와 `d_day`가 null일 수 있다. ADR-0004를 고쳤고, 프론트 하네스(comp-fe D-10/D-11)도 develop에 반영했다(b769f10).

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- **코드포코리아 일시는 그대로 마감일로 쓴다.** 시트 설명이 '행사 시작시간 또는 모집 마감시간'이라 대체 값에 가깝다. 다만 소스가 주는 유일한 날짜이고 사용자 결정 범위(못 찾은 경우)에는 들지 않아 바꾸지 않았다. 바꾸려면 Q-27을 다시 정해야 한다.
- **마감일을 모르는 대회의 마감 판정은 대회 끝나는 날로 한다(구현 결정, Q-35에 기록).** 마감일로 보여 주지는 않지만, 대회가 끝난 뒤에도 '접수 중'으로 남는 것을 막는다. 날짜가 하나도 없는 대회(코드포코리아 '미정')는 소스가 행을 지울 때까지 목록 맨 뒤에 남는다.
- `closes_on`은 최상위 값에서 계산한 날짜다. ADR-0005의 '상태는 저장하지 않는다'는 지킨다(상태가 아니라 날짜다).

### 확인한 것
- 명세: F2-1·F2-3·F2-4·F4-1 문구에 Q-35를 반영했다. 기능 상태는 바뀌지 않는다.
- 결정: Q-35를 추가했다. Q-14는 대체됐다고 표시했다. Q-2(판정·대표 값), Q-26(이벤터스), Q-27(코드포코리아), ADR-0004(필드), ADR-0005(contests 표·인덱스)를 고쳤다.
- 아키텍처: 판정 규칙(`closes_on`, `fill_competition_deadline`)은 `rules.py`에 두었고, repository는 그 값을 저장·조회만 한다. AC-1~3 유지.
- 정확성: `closes_on` 경계를 테스트했다(대회 끝나는 날은 접수 중, 다음 날 마감). `date.max`는 KST 자정으로 저장해도 BSON 범위 안이다. `_write`의 갱신 시각 비교에 `closes_on`이 들어가지만, 이전 문서는 수집 전에 채우므로 불필요한 갱신이 생기지 않는다.
- 보안: 해당 없음. 비밀 값·토큰을 다루지 않는다.
- 주석: 새로 쓰거나 고친 함수·테스트에 한 줄 docstring을 달았다.
- 테스트:
  - 단위: 기간 4, 중복 제거 규칙 5, 수집 2, API 1, 크롤러 3(이벤터스 신규, Dev Event·코드포코리아 수정).
  - Mongo 통합 4: null 저장·정렬, 마감·삭제, 미병합, `closes_on` 채움. 로컬은 `MONGODB_TEST_URI`가 없어 건너뛰었고 CI에서 돈다.
- Git 규약: 이슈 #56, `feature/56_unknown-deadline`, 커밋 `⚡`/`📝`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` / `uv run ruff check .` | 통과 |
| `uv run mypy` | 통과 (65 files) |
| `uv run lint-imports` | 3 kept, 0 broken |
| `uv run pytest` | 315 passed, 28 skipped (Mongo 통합, 로컬 URI 없음) |
| comp-fe `npm run lint` / `npm run build` | 통과 |
