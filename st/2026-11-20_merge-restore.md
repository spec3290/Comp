# 코드 리뷰: 중복 문서 합치기 실패 시 복원 범위 확대

- 날짜: 2026-11-20
- 브랜치: `feature/66_merge-restore` (이슈 #66)
- 기능: F2-2(중복 제거), F2-1(크롤링 실패는 기존 데이터를 건드리지 않음)
- 결정: 없음

## 배경
PR #61 CodeRabbit 리뷰: `ContestRepository._merge`는 흡수할 중복 문서를 먼저 지우고 base 를 쓰는데, 지운 문서를 되살리는 처리가 `_Conflict`에만 걸려 있었다. base 쓰기 중 `DuplicateKeyError`·연결 오류(`AutoReconnect` 등)가 나면 지운 문서와 그 출처가 사라진다. 현재 develop 코드에서 그대로 확인돼 유효한 지적으로 판단했다.

## 바뀐 것
- `app/contests/repository.py` `_merge`: `except _Conflict:` → `except Exception:`. 복원 때 이미 같은 출처가 다른 문서에 들어간 경우 `DuplicateKeyError`를 무시하는 기존 보호와, 원래 예외를 다시 던지는 동작(`raise`)은 그대로다. `save`의 재시도는 여전히 `_Conflict`만 다시 하고 다른 예외는 호출자(수집의 소스별 실패 처리)로 올라간다. docstring 에 이유를 적었다.
- `tests/test_contest_repository.py`: `_write`가 `AutoReconnect`를 던지는 저장소로 저장하면 원래 오류가 올라오고 두 문서가 저장 전과 똑같이 남는지 확인하는 테스트 추가.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- 복원(`insert_one`) 자체가 연결 오류로 실패하면 그 오류가 원래 예외를 대신해 올라가고(원래 예외는 `__context__`에 남는다) 남은 문서 복원도 멈춘다. 연결이 끊긴 상태라 더 할 수 있는 일이 적어 이번 변경 범위에서는 두었다.

### 확인한 것
- 명세: F2-2 같은 대회 묶음에서 출처를 잃지 않음, F2-1 실패 시 기존 데이터 유지와 맞다.
- 아키텍처: repository 안의 변경. 계약 영향 없음.
- 보안·시간: 해당 없음.
- 주석: 고친 함수 docstring 갱신, 새 테스트 클래스·함수에 한 줄 docstring.
- 테스트: 기존 `_Conflict` 복원 테스트(`test_failed_merge_restores_absorbed_document`) 유지 + 경합 외 오류 테스트 추가, `@pytest.mark.feature("F2-2")`.
- Git 규약: 이슈 #66, `feature/66_merge-restore`, 커밋 `🐛`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` / `uv run ruff check .` | 로컬에 `uv`가 없어 CI에서 확인: 통과 |
| `uv run mypy` | CI 통과 |
| `uv run lint-imports` | CI 통과 |
| `uv run pytest` | CI 첫 실행에서 새 테스트 실패: 비교 기준을 `ensure_indexes()`(이전 구조 문서 이전) 전에 떠서 문서 모양이 달랐다. 기준을 그 뒤로 옮겨 CI 통과 |
| `python scripts/check_conventions.py` | CI 통과 |
