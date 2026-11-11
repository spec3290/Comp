# 코드 리뷰: 참가 바로가기

- 날짜: 2026-11-12
- 브랜치: `feature/44_apply-link` (이슈 #44)
- 기능: F2-5(참가 바로가기)
- 결정: Q-29 신설(링크를 못 가져온 대회는 `apply_url: null`로 남기고 프론트가 직접 검색 안내, 사용자 결정), Q-26·Q-27의 '링크 없으면 건너뜀'을 Q-29로 바꿈, ADR-0004·0005 갱신

## 배경
다섯 소스는 이미 참가 링크를 수집하고 있었다(Dev Event `event_link`, DACON 대회 페이지, Devpost `{slug}.devpost.com`, 이벤터스 행사 페이지·외부 신청 링크, 코드포코리아 홈페이지). 그래서 크롤러에 새 수집 로직을 넣을 필요는 없었다. 대신 링크를 못 찾은 대회(이벤터스 외부 접수에 신청 버튼이 없거나 상세 페이지를 못 읽은 경우, 알 수 없는 접수 방식, 코드포코리아 홈페이지 칸이 빈 줄)를 지금까지 **버리던** 것을 `apply_url: null`로 저장하도록 바꿨다. 프론트는 null이면 버튼 대신 "직접 검색해서 찾아 주세요" 안내를 띄운다.

흐름: 상세·목록 응답의 `apply_url`(null 가능) → 버튼 클릭 → `POST /contests/{id}/clicks`(인증 선택) → 클릭 기록(`contest_clicks`, 마이그레이션 `0004`) → `{ apply_url }` → 프론트가 새 창으로 연다.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
1. **프론트 구현은 이 PR 밖** — `comp-fe`에는 아직 대회 화면·API 클라이언트가 없다(Vite 기본 화면뿐). 계약: `apply_url == null`이면 안내 문구, `status == closed`면 버튼 비활성, 그 밖이면 클릭 핸들러에서 `window.open()`으로 창을 먼저 열고 `POST /contests/{id}/clicks` 응답의 주소를 넣는다(비동기 응답 뒤에 열면 팝업 차단에 걸린다).
2. **클릭 수 부풀리기** — 인증이 선택이고 매번 한 건씩 기록하므로 같은 사람이 반복 호출하면 수가 늘어난다. 1차에는 집계 화면이 없어 그대로 두었다. 순위·추천에 쓰게 되면 회원·IP별 중복 제거나 요청 제한을 검토한다.
3. **오래된 링크** — 다시 수집할 때 링크를 못 찾으면 저장된 링크를 지우지 않는다(F2-1: 일시 장애로 기존 데이터를 망가뜨리지 않음). 그래서 주최자가 실제로 신청 링크를 내린 경우에도 예전 링크가 남는다. 일시 장애와 실제 삭제를 구분할 근거가 없어 안전한 쪽을 택했다.
4. **탈퇴와 클릭 사이 경합** — 탈퇴 직후 아직 유효한 Access(최대 15분)로 클릭하면 FK 위반으로 500이 날 수 있다. 회원 탈퇴 리뷰(2026-11-12)의 북마크와 같은 경우이고 15분 뒤에는 생기지 않는다.

### 확인한 것
- 명세: F2-5 수용 기준(공식 URL 새 창, 클릭 기록, 마감 대회 거절) + 링크 없는 대회 안내를 구현하고 상태를 `done`으로 바꿨다. 마감 당일은 허용.
- 결정: ADR-0004 엔드포인트 초안의 `POST /contests/{id}/clicks → { apply_url }`를 그대로 따랐고 에러 코드(`CONTEST_CLOSED`, `APPLY_URL_MISSING`, `NOT_FOUND`)와 응답 필드 null 가능을 적었다. ADR-0005 contests.apply_url null 가능, contest_clicks 컬럼·FK·마이그레이션을 적었다. 클릭의 인증 선택·매번 기록·탈퇴 시 삭제는 (가정)으로 표시했다.
- 아키텍처: 라우터 → `ApplyService` → `ClickRepository`·`ContestRepository`. 마감 판정은 기존 `_view`(rules의 `ApplyPeriod`)를 재사용. 링크 유지·채우기는 저장소의 조건부 단일 연산(`apply_url: None` 조건 `$set`)이라 동시 수집에도 덮어쓰지 않는다. AC-1~3 유지, `noqa`·`type: ignore` 추가 없음.
- 보안·정확성: 시간은 `now_kst()`·고정 `today` 의존성. 응답·로그에 토큰·개인정보 없음. 상세 페이지 실패는 그 항목만 링크 없이 수집하고 기존 링크는 유지한다. 예전 문서에 `apply_url` 키가 없어도 `doc.get`으로 null 처리.
- 주석: 새로 쓰거나 고친 모듈·클래스·함수 모두 한 줄 docstring.
- 테스트: `test_apply_link.py` 8건(클릭 시 URL·기록, 회원 id·매번 기록, 마감 당일 허용, 마감 409, 링크 없음 409, 없는 대회 404, 상세의 null 링크, 탈퇴 시 클릭 삭제), 크롤러 3건 수정(이벤터스 링크 없음·상세 실패 시 링크 없이 수집, 코드포코리아 홈페이지 없음), Mongo 통합 3건(null 링크 저장, 재수집 시 링크 유지, 다른 소스 링크로 채우기). 네트워크 호출 없음.
- Git 규약: 이슈 #44, `feature/44_apply-link`, 커밋 `⚡`/`📝`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` | 통과 (81 files already formatted) |
| `uv run ruff check .` | 통과 |
| `uv run mypy` | 통과 (64 source files) |
| `uv run lint-imports` | 통과 (3 kept, 0 broken) |
| `uv run pytest -q` | 288 passed, 19 skipped (`MONGODB_TEST_URI` 미설정으로 Mongo 통합 테스트 건너뜀, CI에서 실행) |
