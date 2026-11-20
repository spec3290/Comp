# 코드 리뷰: 참가 바로가기 클릭 기록·엔드포인트 삭제

- 날짜: 2026-11-20
- 브랜치: `feature/70_remove-contest-clicks` (이슈 #70), `feature/68_remove-grade-school-type`(PR #69) 위에 쌓음
- 기능: F2-5(참가 바로가기), F1-8(탈퇴 시 삭제 대상)
- 결정: Q-39 추가(2026-11-20 사용자 결정), Q-30의 클릭 부분 삭제 표시

## 배경
사용자 질문: "굳이 클릭 수를 할 필요가 있나. 그냥 제거하는거 어때?", "왜 개발한거야 이거?". F2-5 "클릭 수 기록"은 2026-10-23 첫 명세(`ca02f70`)부터 있었지만 쓰임새가 없었고, #44/#45 구현 리뷰(`st/2026-11-12_apply-link.md`)에도 "1차에는 집계 화면이 없어"라고 적힌 채 넘어갔다. 그때 Q-번호로 쓰임새를 물었어야 했다. 이번에 엔드포인트까지 지우기로 했다(사용자 결정). `apply_url`은 이미 목록·상세 응답에 있다.

## 바뀐 것
- 삭제: `ContestClick` 모델, `ClickRepository`, `ApplyService`·`get_apply_service`, `POST /contests/{id}/clicks` 라우트, `ApplyLinkOut`, `ApplyUrlMissingError`(`APPLY_URL_MISSING`), `tests/unit/test_apply_link.py`. `ContestClosedError`는 북마크(Q-18)가 쓰므로 남겼다.
- `migrations/versions/0007_drop_contest_clicks.py`: 인덱스·테이블 삭제. downgrade는 `0004`와 같은 빈 테이블을 되살린다.
- 문서: features.md F2-5(프론트가 `apply_url`로 바로 연다, 탈퇴 시 클릭 삭제 문구 삭제), decisions.md API 규약·엔드포인트 표·contest_clicks 행·ADR-0008 테이블 목록·Q-2·Q-30, Q-39 추가.
- 스킬: code-review 점검표에 **쓰임새** 항목 추가(저장하는 데이터마다 읽는 기능이 명세에 있는지, 없으면 Q-번호로 묻기). 같은 실수(Q-38, Q-39)가 두 번 나와서 점검표로 올렸다. feature-implementation 예시 테스트 이름을 북마크로 바꿨다.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- **머지 순서**: PR #69(`0006`)를 먼저 머지한다. 이 PR의 `0007`이 `0006`을 잇고, #69 머지 전에는 이 PR diff에 #69 커밋이 같이 보인다.
- **배포 순서**: 프론트가 `POST /contests/{id}/clicks`를 부르고 있다면 **새 프론트(apply_url 직접 열기) → 새 API → `0007`** 순서다. 예전 API가 떠 있는 동안 `0007`을 돌리면 클릭 요청이 500이 되니, 마이그레이션은 예전 API 인스턴스가 모두 내려간 뒤에 돌린다(#69와 같은 이유). `comp-fe`에 아직 대회 화면이 없다면(2026-11-12 기준) 순서 걱정은 없다.
- 운영 DB의 클릭 행은 `0007`로 사라진다. 남겨 둘 필요가 있으면 그 전에 덤프한다.

### 확인한 것
- 명세: F2-5 나머지 수용 기준(링크 없는 대회 `apply_url: null` 노출, 재수집 때 링크 유지·다른 소스로 채움)은 수집·저장소 코드라 그대로다. 상태 `done` 유지(`tests/test_contest_repository.py`의 F2-5 테스트 3건).
- 결정: Q-39 추가, ADR-0004(API)·ADR-0005(데이터 모델)·ADR-0008(저장소 분담) 문구를 같은 변경에서 고쳤다.
- 아키텍처: 라우터·서비스·저장소에서 같은 유스케이스를 함께 지웠다. 계약 변경 없음. AST로 쓰지 않게 된 import가 없음을 확인했다.
- 보안: 회원 행동 기록이 없어졌다. 탈퇴(F1-8)는 FK CASCADE에 맡겼으므로 auth 코드 변경 없음.
- 주석: 새 마이그레이션 모듈·함수에 docstring.
- 테스트: 삭제된 기능의 테스트만 지웠다. `grep -rni "clicks|ApplyLink|ApplyService|APPLY_URL_MISSING|ContestClick" app tests` 결과 없음.
- Git 규약: 이슈 #70, `feature/70_remove-contest-clicks`, 커밋 `⚰️`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `python3 -m py_compile` (바뀐 파일) | 통과. 로컬에 `uv`가 없어 나머지는 CI에서 확인 |
| `uv run ruff format --check .` / `uv run ruff check .` | CI에서 확인 |
| `uv run mypy` | CI에서 확인 |
| `uv run lint-imports` | CI에서 확인 |
| `uv run pytest` | CI에서 확인 |
| `python scripts/check_conventions.py` | CI에서 확인 |
