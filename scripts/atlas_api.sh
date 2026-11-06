# collect.yml 이 source 해서 쓰는 Atlas Admin API 호출 함수.
# 필요한 환경변수: ATLAS_AUTH(공개키:비밀키), ATLAS_ACCEPT, RUNNER_TEMP
# 실패하면 재시도하지 않고 HTTP 상태와 Atlas 오류 본문의 errorCode·reason·detail 만 남긴다.
# 요청 헤더·자격 증명은 출력하지 않는다.
#
# 사용: atlas_api METHOD URL [curl 추가 인자...]   응답 본문은 "$ATLAS_BODY" 파일에 남는다.

ATLAS_BODY="$RUNNER_TEMP/atlas-response.json"

atlas_api() {
  local method="$1" url="$2" code summary
  shift 2
  code="$(curl -sS --digest -u "$ATLAS_AUTH" -H "$ATLAS_ACCEPT" -H "Content-Type: application/json" \
    -o "$ATLAS_BODY" -w '%{http_code}' -X "$method" "$url" "$@")"
  if [ "${code:0:1}" = "2" ]; then
    return 0
  fi
  summary="$(jq -r '[.errorCode, .reason, .detail] | map(select(. != null)) | join(" / ")' "$ATLAS_BODY" 2>/dev/null || true)"
  echo "::error::Atlas API $method 실패: HTTP $code ${summary:-(오류 본문 없음)}"
  return 1
}
