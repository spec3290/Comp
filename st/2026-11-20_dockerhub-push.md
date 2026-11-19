# 코드 리뷰: Docker Hub로 이미지 자동 push

- 날짜: 2026-11-20
- 브랜치: `feature/64_dockerhub-push` (이슈 #64, `feature/62_docker-image` 위에 쌓음)
- 기능: 없음 (프로젝트 설정)
- 결정: Q-36 갱신 — 레지스트리는 Docker Hub(사용자 결정), 배포 대상은 열림

## 배경
사용자 결정: 이미지 레지스트리로 Docker Hub를 쓴다. 이미지(#62, PR #63)를 CI가 자동으로 빌드해 올리게 한다.

## 바뀐 것
- `.github/workflows/image.yml` 추가: develop push와 `v*` 태그 push(그리고 수동 실행) 때 빌드해 `{DOCKERHUB_USERNAME}/comp`로 push한다. 태그는 `develop`·`sha-<7자리>`, 릴리즈는 `1.2.3`·`1.2`·`latest`(docker/metadata-action). 빌드 캐시는 GitHub Actions 캐시를 쓴다. 설정이 비어 있으면 첫 단계에서 무엇이 빠졌는지 알려 주고 실패한다.
- `docs/decisions.md` Q-36: 레지스트리 결정 내용과 태그 규칙.
- `README.md`: Docker Hub 이미지 이름, 태그 규칙, 필요한 Secrets/Variables.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- 머지 전에 저장소에 Variable `DOCKERHUB_USERNAME`, Secret `DOCKERHUB_TOKEN`을 넣는다. 없으면 develop 머지 때마다 이 워크플로가 실패로 표시된다(다른 CI에는 영향 없음).
- 토큰은 이 저장소 하나에만 Read & Write 권한으로 만든다. Docker Hub 저장소를 공개로 둘지 비공개로 둘지 정한다(이미지에 비밀 값은 없다).
- PR #63을 먼저 머지한다. 이 PR의 diff에는 그 전까지 #63의 커밋이 같이 보인다.

### 확인한 것
- 명세: 기능 변경 없음.
- 결정: 버전 태그는 git-workflow 스킬의 릴리즈 규칙(`🚀 :: v1.0.0` + 같은 이름 태그)을 따른다.
- 아키텍처: 앱 코드 변경 없음.
- 보안: 토큰은 Secrets로만 받고 로그에 찍지 않는다(아이디는 비밀이 아니라 Variables). PR 이벤트에서는 돌지 않아 포크 PR이 토큰을 쓸 수 없다. 워크플로 권한은 `contents: read`.
- 주석: 워크플로 머리말에 필요한 설정·태그 규칙을, 설정 확인 단계에 이유를 적었다.
- 테스트: 이미지 동작은 ci.yml `image` 잡(#62)이 PR에서 확인한다. push 워크플로 자체는 develop 머지 후 첫 실행에서 확인된다.
- Git 규약: 이슈 #64, `feature/64_dockerhub-push`, 커밋 `⚙️`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run mypy` / `uv run lint-imports` / `uv run pytest` | 로컬에 `uv`가 없어 실행하지 못함. CI에서 확인 (Python 코드 변경 없음) |
| `image.yml` push 실행 | PR 이벤트에서는 돌지 않는다. develop 머지 후 첫 실행에서 확인 |
| `python scripts/check_conventions.py` | CI에서 확인 |
