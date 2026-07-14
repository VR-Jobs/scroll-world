#!/bin/sh
set -eu

usage() {
  echo "usage: $0 <task-dir> <output-video> <output-last-frame>" >&2
  exit 2
}

test "$#" -eq 3 || usage
TASK_DIR="$1"
OUTPUT_VIDEO="$2"
OUTPUT_LAST_FRAME="$3"
TASK_ID="$TASK_DIR/task-id.txt"
META="$TASK_DIR/task-meta.json"
RESPONSE="$TASK_DIR/status-response.json"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
TOOL="$SCRIPT_DIR/sw_tool.py"
LEDGER="${SW_LEDGER_FILE:-.work/usage-ledger.json}"

: "${ARK_API_KEY:?set ARK_API_KEY in the environment or source .env.local}"
API_BASE="${ARK_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
test -s "$TASK_ID" || { echo "missing task id: $TASK_ID" >&2; exit 2; }
test -s "$META" || { echo "missing task metadata: $META" >&2; exit 2; }
OPERATION_ID=$(jq -r '.operation_id // empty' "$META")
test -n "$OPERATION_ID" || { echo "task metadata missing operation_id" >&2; exit 2; }

if test -s "$OUTPUT_VIDEO" && test -s "$OUTPUT_LAST_FRAME"; then
  if test -s "$RESPONSE" && test "$(jq -r '.status // empty' "$RESPONSE")" = succeeded; then
    python3 "$TOOL" complete-video --ledger "$LEDGER" --operation-id "$OPERATION_ID" --response "$RESPONSE"
  fi
  echo "video cached: $OUTPUT_VIDEO"
  exit 0
fi

id=$(cat "$TASK_ID")
curl_rc=0
code=$(curl -sS -o "$RESPONSE" -w '%{http_code}' \
  -H "Authorization: Bearer $ARK_API_KEY" \
  "$API_BASE/contents/generations/tasks/$id") || curl_rc=$?
if test "$curl_rc" -ne 0; then
  echo "video task query transport failed; task id preserved: $id" >&2
  exit 3
fi
if test "$code" != 200; then
  echo "video task query failed: HTTP $code" >&2
  jq -r '.error.message // .message // .error.code // "unknown API error"' "$RESPONSE" >&2
  exit 3
fi

status=$(jq -r '.status // empty' "$RESPONSE")
case "$status" in
  queued|running)
    echo "video task $id: $status"
    exit 10
    ;;
  succeeded)
    python3 "$TOOL" complete-video --ledger "$LEDGER" --operation-id "$OPERATION_ID" --response "$RESPONSE"
    video_url=$(jq -r '.content.video_url // empty' "$RESPONSE")
    frame_url=$(jq -r '.content.last_frame_url // empty' "$RESPONSE")
    test -n "$video_url" || { echo "successful task missing content.video_url" >&2; exit 4; }
    test -n "$frame_url" || { echo "successful task missing content.last_frame_url" >&2; exit 4; }
    mkdir -p "$(dirname "$OUTPUT_VIDEO")" "$(dirname "$OUTPUT_LAST_FRAME")"
    if ! test -s "$OUTPUT_VIDEO"; then
      part="$OUTPUT_VIDEO.part.$$"
      trap 'rm -f "$part"' EXIT HUP INT TERM
      curl -fsSL "$video_url" -o "$part"
      mv "$part" "$OUTPUT_VIDEO"
      trap - EXIT HUP INT TERM
    fi
    if ! test -s "$OUTPUT_LAST_FRAME"; then
      part="$OUTPUT_LAST_FRAME.part.$$"
      trap 'rm -f "$part"' EXIT HUP INT TERM
      curl -fsSL "$frame_url" -o "$part"
      mv "$part" "$OUTPUT_LAST_FRAME"
      trap - EXIT HUP INT TERM
    fi
    next_meta="$META.next"
    jq '.status="succeeded"' "$META" > "$next_meta"
    mv "$next_meta" "$META"
    echo "video task $id: downloaded"
    ;;
  failed|expired|cancelled|canceled)
    python3 "$TOOL" fail --ledger "$LEDGER" --operation-id "$OPERATION_ID" --reason "task $status"
    next_meta="$META.next"
    jq --arg status "$status" '.status=$status' "$META" > "$next_meta"
    mv "$next_meta" "$META"
    echo "video task $id: $status" >&2
    jq -r '.error.message // .message // "task did not succeed"' "$RESPONSE" >&2
    exit 5
    ;;
  *)
    echo "video task $id: unknown status '$status'" >&2
    exit 6
    ;;
esac
