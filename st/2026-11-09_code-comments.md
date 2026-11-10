# 코드 리뷰: 전체 코드 설명 주석 추가 + 주석·리뷰 기록 규칙 하네스 반영

- 날짜: 2026-11-09
- 브랜치: `feature/35_code-comments` (이슈 #35, 처음엔 `feature/33_devpost-korea-offline` 작업 트리에서 작성 → 권장 1로 옮김)
- 범위: `app/`, `tests/`, `migrations/`, `scripts/`의 Python 53개 파일, `AGENTS.md`, `.claude/skills/feature-implementation`, `.claude/skills/code-review`

## 리뷰 결과: approve (조건: 권장 1 처리 후 PR)

### 반드시 수정 (blocker)
- 없음

### 권장
1. **브랜치 분리** — 이 변경은 이슈 #33(Devpost 국내 오프라인)과 상관이 없다. 한 PR이 한 이슈에 대응해야 하므로(git-workflow) 새 이슈(`Chore` 또는 `DOCS`)와 `feature/{번호}_code-comments` 브랜치로 옮겨 커밋한다.
2. **CI로 올리기** — 주석 규칙은 지금 문서에만 있다. 조정 규칙 5에 따라 같은 누락이 반복되면 `pyproject.toml`의 ruff `select`에 `"D1"`(pydocstyle의 docstring 누락 검사, D100~D107)을 넣어 CI가 막게 한다. 단 D1은 `_` 내부 함수를 검사하지 않으므로 그 부분은 리뷰로 계속 본다.
3. **`pyproject.toml` 미커밋 변경** — 작업 전부터 있던 `[tool.setuptools.packages.find]` 추가분은 이번 작업과 무관해서 건드리지 않았다. 파일 끝 개행이 없고 주석 문체(`~뜻입니다.`)가 다른 주석과 다르다.
4. **docstring 정확도** — 429곳을 짧은 설명으로 채웠다. 큰 함수는 본문 앞부분을 보고 썼으므로, 다음에 그 함수를 고치는 사람이 설명이 맞는지 같이 확인한다. 리뷰 중 실제와 달라 고친 곳은 2곳이다: `UserService`(탈퇴 기능 없음), `test_concurrent_same_nickname_returns_409`(DB 유일 제약과 되돌림 검증).

### 확인한 것
- 명세: 기능 동작 변경 없음. features.md 상태 변경 대상 아님.
- 결정: 새 결정 없음. 열린 Q-항목을 건드리지 않음.
- 아키텍처: import 추가·삭제 없음. AC-1~3 영향 없음.
- 보안: 비밀 값·토큰·개인정보를 docstring에 넣지 않음.
- 주석: 모듈·클래스·함수 docstring 누락 429 → 0 (ast로 전 파일 검사). 기존 docstring은 그대로 둠. `class Base`의 불필요한 `pass` 1곳은 docstring이 본문이 되므로 지움.
- 동작 동일성: HEAD와 작업본을 docstring·`pass`를 뺀 AST로 비교, 53개 파일 모두 같음.
- 테스트: 동작 변경이 없어 테스트를 추가하지 않음.
- Git 규약: 이슈 #35, `feature/35_code-comments`에서 하네스 규칙 / 코드 주석 2개 커밋. `pyproject.toml` 미커밋 변경은 넣지 않음 (권장 3).

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` | 통과 (103 files already formatted) |
| `uv run ruff check .` | 통과 |
| `uv run python -m mypy` | 통과 (58 source files) |
| `uv run lint-imports` | 로컬 실행 불가: Windows 애플리케이션 제어 정책이 grimp DLL을 막음. import 변경이 없으므로 CI 결과로 확인 |
| `uv run python -m pytest -q` | 228 passed, 16 skipped (`MONGODB_TEST_URI` 미설정으로 Mongo 통합 테스트 건너뜀) |

## PR #36 CodeRabbit 리뷰 반영
- `app/core/log_redaction.py` `RedactQueryFilter.filter`: 인자가 있으면 메시지는 가리지 않는데 "메시지와 인자를 가린다"로 적었다 → 실제 분기대로 고침. 동작은 그대로 둠(접속 로그는 쿼리를 인자로 넘김).
- `app/contests/service.py` `_collect_one`: "실패하면 로그만 남긴다"였지만 `CollectResult.error`도 채워 돌려준다 → 설명 보강.
- `tests/unit/test_token_refresh.py` `test_concurrent_consume_...`: 호출이 순차인데 "동시에"라고 적었다 → 두 번 소비로 고침.
- `code-review` 스킬: `git diff`만으로는 스테이징·새 파일을 놓친다 → `git status` + `git diff HEAD` + 새 파일 직접 읽기로 고침.

## 하네스 변경
- `AGENTS.md` 하드 규칙: (1) 새로 쓰거나 고친 모듈·클래스·함수에 한 줄 docstring, (2) 작업이 끝나면 `st/YYYY-MM-DD_{todo}.md`에 code-review 형식으로 리뷰를 남기고 같은 커밋에 넣음. 하네스 구성 표에 `st/` 추가.
- `feature-implementation`: 5단계에 docstring, 8단계 리뷰 기록 추가(커밋·PR은 9단계로 밀림).
- `code-review`: 점검표에 `주석` 항목, 결과를 `st/`에 저장하는 절차, PR 전 리뷰는 `git diff`로 읽는다는 안내 추가.
