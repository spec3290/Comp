#!/usr/bin/env bash
# EC2 에서 지정한 이미지로 마이그레이션한 뒤 API 를 바꾼다 (docs/decisions.md Q-37). deploy.yml 이 SSH 로 부른다.
#
#   IMAGE=<아이디>/comp TAG=sha-abc1234 bash ~/comp/deploy.sh
#
# DOCKERHUB_USERNAME 을 주면 표준 입력의 토큰으로 로그인해 받은 뒤 바로 로그아웃한다(비공개 저장소용).
# SKIP_PULL=1 이면 받지 않고 로컬 이미지를 쓰며 이미지 정리도 하지 않는다(방금 빌드한 이미지로 이 스크립트를 시험할 때).
# 마이그레이션이 실패하면 API 를 바꾸지 않아 이전 버전이 계속 돈다.
set -euo pipefail
cd "$(dirname "$0")"

: "${IMAGE:?IMAGE 가 없다}" "${TAG:?TAG 가 없다}"
export IMAGE TAG
[ -f .env ] || { echo "$(pwd)/.env 가 없다. README 'EC2 자동 배포'를 보고 먼저 만든다" >&2; exit 1; }

# 셸 환경의 IMAGE·TAG 가 release.env(이전 배포) 값보다 우선한다
compose() {
  local files=(--env-file .env)
  [ -f release.env ] && files+=(--env-file release.env)
  docker compose "${files[@]}" "$@"
}

# 비공개 저장소면 받는 동안만 로그인한다. 서버에 토큰을 남기지 않으려고 바로 로그아웃한다
pull_images() {
  if [ "${SKIP_PULL:-0}" = "1" ]; then
    return
  fi
  if [ -n "${DOCKERHUB_USERNAME:-}" ]; then
    docker login --username "$DOCKERHUB_USERNAME" --password-stdin > /dev/null
    trap 'docker logout > /dev/null 2>&1 || true' EXIT
  fi
  compose pull --quiet
  if [ -n "${DOCKERHUB_USERNAME:-}" ]; then
    docker logout > /dev/null
    trap - EXIT
  fi
}

echo "배포: $IMAGE:$TAG"
pull_images
compose up -d --wait postgres redis
compose run --rm migrate
if ! compose up -d --wait --wait-timeout 120 --no-deps api; then
  echo "API 가 healthy 가 되지 않았다" >&2
  compose logs --tail 100 api >&2
  exit 1
fi

# 재부팅 뒤 수동 실행이나 다음 배포가 같은 이미지를 쓰도록 마지막 성공 배포를 남긴다
printf 'IMAGE=%s\nTAG=%s\n' "$IMAGE" "$TAG" > release.env
# 서버 디스크가 sha 태그로 차지 않게 3일 지난, 컨테이너가 쓰지 않는 이미지를 지운다.
# 로컬 이미지 시험(SKIP_PULL=1)에서는 개발자 PC 의 다른 이미지를 지우지 않게 건너뛴다
if [ "${SKIP_PULL:-0}" != "1" ]; then
  docker image prune -af --filter "until=72h" > /dev/null
fi
compose ps
