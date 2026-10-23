---
name: git-workflow
description: 이슈 생성, 브랜치 생성, 커밋 메시지 작성, PR 생성·머지, 릴리즈 버전 올리기 등 Git/GitHub 작업을 할 때 사용한다. 커밋·브랜치·PR 규약이 필요하면 항상 이 스킬을 따른다.
---

# Git 워크플로 (Github Commit 명세)

기본은 GitFlow다. 아래 규약은 `.github/workflows/ci.yml`의 `scripts/check_conventions.py`가 PR에서 검사한다.

## 개발 프로세스

1. **이슈 생성** — 한 기능 당 이슈 하나. 성격에 맞는 label을 붙인다.
2. **이슈 기반 브랜치 생성** — `feature/{이슈번호}_{todo}` (긴급 수정은 `hotfix/{이슈번호}_{todo}`).
3. 이슈와 관련된 기능 개발을 완료한다.
4. PR 또는 merge commit을 만들 때도 커밋 메시지 규칙을 지킨다.
5. PR이 주요 브랜치로 merge되려면: 최소 1명의 review approve, CI로 인한 build success. 테스트 케이스 작성은 optional이지만 이 프로젝트는 기능 테스트를 기본으로 쓴다.
6. merge 후 feature 브랜치를 제거한다.
7. 이슈를 close한다. GitFlow에서 PR 대상이 default 브랜치가 아니면 자동으로 닫히지 않으므로 직접 닫는다.

```bash
gh issue create --title "소셜 로그인 기능 개발" --label feature --body "F1-6 수용 기준 ..."
git switch -c feature/12_social-login origin/develop
# ... 개발 ...
gh pr create --base develop --title "🔀 :: (#12) 소셜 로그인 기능 추가" --body "F1-6 구현. 이슈 #12"
# merge 후
git push origin --delete feature/12_social-login
gh issue close 12
```

`{todo}`는 하려는 일을 짧게 쓴 슬러그다 (공백 없이, 예: `social-login`).

## 이슈 규칙

- 한 기능 당 하나의 issue를 원칙으로 한다. 기능 ID(F1-6 등)를 제목이나 본문에 적는다.
- 새 리포지토리를 만들면 label을 아래처럼 설정한다.

| Label | Description | Color | 의미 |
|---|---|---|---|
| BUG | Something isn't working | #d73a4a | 예기치 않은 문제 또는 의도하지 않은 동작(버그) |
| feature | New feature or request | #a701d2 | 새로운 기능 요청 |
| DOCS | Improvements or additions to documentation | #cfd3d7 | 문서를 개선하거나 추가할 필요 |
| Chore | | #0075ca | 자잘한 수정 |
| Refactor | Refactoring is needed | #008672 | 리펙토링 필요 |
| 프로젝트 설정 | Project setting is needed | #555303 | 프로젝트 설정 필요 |
| 테스트 추가 | Request a new test | #CD7F57 | 새로운 테스트 요청 |

```bash
gh label create BUG --color d73a4a --description "Something isn't working"
gh label create feature --color a701d2 --description "New feature or request"
gh label create DOCS --color cfd3d7 --description "Improvements or additions to documentation"
gh label create Chore --color 0075ca
gh label create Refactor --color 008672 --description "Refactoring is needed"
gh label create "프로젝트 설정" --color 555303 --description "Project setting is needed"
gh label create "테스트 추가" --color CD7F57 --description "Request a new test"
```

## 커밋 메시지

형식: `CommitType :: (#issue number) Subject` — `(#issue number)`는 선택 위치에 둔다.

| CommitType | 설명 |
|---|---|
| 📑 | 파일 생성 및 구조 변경 |
| ⚡ | 기능 업데이트 |
| ⚰️ | 기능 삭제 |
| 🐛 | 버그 수정 |
| ♻️ | 코드 리펙토링 |
| 📝 | 문서 작성 및 수정 |
| ⚙️ | 프로젝트 세팅 |
| 🧪 | 테스트 코드 추가 |
| 🚀 | 새 버전 릴리즈 (커밋은 아니지만) |
| 🔀 | Merge or PR |

**issue number**
- merge commit이나 PR을 날릴 때에만 사용한다. 괄호 `( )` 안에 쓰고, `#` 뒤에 브랜치가 기반으로 둔 이슈 번호를 적는다: `(#12)`.
- 🔀 merge/PR에는 필수, 일반 커밋에는 선택으로 해석한다. 원문 예시에 일반 커밋에도 `(#19)`가 있어 어긋난다 ([Q-11](../../../docs/decisions.md)).

**Subject**
- 50자를 넘기지 않고, 명령형으로, 한국어로 쓴다.
- `어떻게`보다 `무엇을`, `왜`에 초점을 둔다.
- `"~수정 했다"` → `"~수정"`, `"Add~"` → `"~추가"`.

```
🐛 :: [MainPage] 헤더가 안보이는 버그 수정
🐛 :: (#19)[FeedServiceImpl] 피드 버그 로직 수정
🚀 :: v1.0.0
📝 :: README 파일 수정
🔀 :: (#12) 소셜 로그인 기능 추가
```

## 브랜치 규칙

- 기본적으로 GitFlow를 따른다.
- Feature: `feature/{issue number}_{todo}`, HotFix: `hotfix/{issue number}_{todo}`.
- `main`, `develop`, `release/*`는 규약 검사에서 예외다.

## 버전 규칙

`X.Y.Z` — Major(API가 깨짐), Minor(API가 안 깨짐), Patches(버그 수정).

- 모든 버전은 `01.00.00`에서 시작한다. 앞자리 0은 생략할 수 있다: `01.01.09` → `1.1.9`, `01.01.10` → `1.1.10`.
- **Major**: 1로 시작. 증가 시 나머지 버전은 초기화. 전체를 뒤엎었을 때 변화한다.
- **Minor**: 00으로 시작. 없던 기능 추가나 기존 기능 수정 등의 변화가 생기면 올린다.
- **Patches**: 00으로 시작. 자잘한 버그나 내부 코드 보완 등의 변화가 생기면 올린다.
- 릴리즈는 `🚀 :: v1.0.0` 형식으로 쓰고 같은 이름의 태그를 단다.

출처: SEMICOLON; & XQUARE팀 'Github Commit 명세' (출처만 남기면 자유롭게 이용 가능).
