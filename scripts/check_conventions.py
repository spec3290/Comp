#!/usr/bin/env python3
"""Git 규약 검사 (.claude/skills/git-workflow/SKILL.md 의 규칙을 CI에서 강제한다).

  python scripts/check_conventions.py --base origin/develop \\
      --head-ref feature/12_login --title "🔀 :: (#12) 로그인 추가"

검사 대상: base..HEAD 의 (merge 제외) 커밋 제목, PR 브랜치명, PR 제목.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

VS16 = "️"  # 이모지 변형 선택자. 있어도 없어도 같은 타입으로 본다.
COMMIT_TYPES = "📑⚡⚰♻⚙🐛📝🧪🚀🔀"
COMMIT_RE = re.compile(rf"^(?P<type>[{COMMIT_TYPES}]) :: (?P<rest>.+)$")
ISSUE_RE = re.compile(r"^\(#(?P<issue>\d+)\)\s*(?P<subject>.*)$")
VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")
HANGUL_RE = re.compile(r"[가-힣]")
BRANCH_RE = re.compile(r"^(feature|hotfix)/\d+_[^\s/]+$")
EXEMPT_BRANCH_RE = re.compile(r"^(main|develop|release/.+)$")
MAX_SUBJECT = 50


def check_commit_message(message: str, *, is_pr_title: bool = False) -> str | None:
    """위반 사유를 돌려준다. 통과하면 None."""
    text = message.strip().replace(VS16, "")
    match = COMMIT_RE.match(text)
    if not match:
        return (
            "형식은 'CommitType :: (#이슈번호) Subject' 여야 한다 "
            "(타입: 📑 ⚡ ⚰️ 🐛 ♻️ 📝 ⚙️ 🧪 🚀 🔀)"
        )

    ctype, rest = match["type"], match["rest"].strip()
    issue = ISSUE_RE.match(rest)
    subject = issue["subject"].strip() if issue else rest

    if ctype == "🚀":
        return None if VERSION_RE.match(subject) else "릴리즈는 '🚀 :: v1.0.0' 형식이어야 한다"
    if (ctype == "🔀" or is_pr_title) and not issue:
        return "merge/PR 은 '(#이슈번호)'가 필요하다 (예: 🔀 :: (#12) 소셜 로그인 추가)"
    if not subject:
        return "Subject 가 비어 있다"
    if len(subject) > MAX_SUBJECT:
        return f"Subject 는 {MAX_SUBJECT}자 이하여야 한다 (현재 {len(subject)}자)"
    if not HANGUL_RE.search(subject):
        return "Subject 는 한국어로 작성한다"
    return None


def check_branch_name(ref: str) -> str | None:
    if EXEMPT_BRANCH_RE.match(ref) or BRANCH_RE.match(ref):
        return None
    return "브랜치명은 'feature/{이슈번호}_{todo}' 또는 'hotfix/{이슈번호}_{todo}' 여야 한다"


def commit_subjects(base: str) -> list[str]:
    out = subprocess.run(  # noqa: S603  고정된 git 인자만 사용
        ["git", "log", "--no-merges", "--format=%s", f"{base}..HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="비교 기준 ref (예: origin/develop). 없으면 커밋 검사 생략")
    parser.add_argument("--head-ref", help="PR 브랜치명")
    parser.add_argument("--title", help="PR 제목")
    args = parser.parse_args()

    errors: list[str] = []
    if args.head_ref and (reason := check_branch_name(args.head_ref)):
        errors.append(f"브랜치 '{args.head_ref}': {reason}")
    if args.title and (reason := check_commit_message(args.title, is_pr_title=True)):
        errors.append(f"PR 제목 '{args.title}': {reason}")
    if args.base:
        for subject in commit_subjects(args.base):
            if reason := check_commit_message(subject):
                errors.append(f"커밋 '{subject}': {reason}")

    if errors:
        print("Git 규약 위반 (.claude/skills/git-workflow/SKILL.md 참고):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Git 규약 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
