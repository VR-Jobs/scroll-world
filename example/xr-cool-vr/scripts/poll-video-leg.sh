#!/bin/sh
set -eu

usage() {
  echo "usage: $0 <preview|final> <hero|optics|tracking|comfort|beyond>" >&2
  exit 2
}

test "$#" -eq 2 || usage
MODE="$1"
LEG="$2"

case "$MODE" in preview|final) ;; *) usage ;; esac
case "$LEG" in hero|optics|tracking|comfort|beyond) ;; *) usage ;; esac

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env.local"
WORK="$ROOT/.work/video-$MODE-$LEG"
TASK_ID="$WORK/task-id.txt"
ASSETS="$ROOT/assets"
VIDEO="$ASSETS/$MODE-$LEG.mp4"
LAST_FRAME="$ASSETS/$MODE-$LEG-last.jpg"

test -f "$ENV_FILE" || { echo "missing $ENV_FILE" >&2; exit 2; }
test -s "$TASK_ID" || { echo "missing task id: $TASK_ID" >&2; exit 2; }

set -a
. "$ENV_FILE"
set +a
: "${ARK_API_KEY:?ARK_API_KEY is not set in .env.local}"

id=$(cat "$TASK_ID")
code=$(curl -sS -o "$WORK/status-response.json" -w '%{http_code}' \
  -H "Authorization: Bearer $ARK_API_KEY" \
  "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/$id")

if [ "$code" != 200 ]; then
  echo "video task query failed: HTTP $code" >&2
  jq -r '.error.message // .message // "unknown API error"' "$WORK/status-response.json" >&2
  exit 3
fi

status=$(jq -r '.status // empty' "$WORK/status-response.json")
case "$status" in
  queued|running)
    echo "$MODE/$LEG: $status"
    exit 10
    ;;
  succeeded)
    mkdir -p "$ASSETS"
    video_url=$(jq -r '.content.video_url // empty' "$WORK/status-response.json")
    frame_url=$(jq -r '.content.last_frame_url // empty' "$WORK/status-response.json")
    test -n "$video_url" || { echo "successful task missing video_url" >&2; exit 4; }
    test -n "$frame_url" || { echo "successful task missing last_frame_url" >&2; exit 4; }
    test -s "$VIDEO" || curl -fsSL "$video_url" -o "$VIDEO"
    test -s "$LAST_FRAME" || curl -fsSL "$frame_url" -o "$LAST_FRAME"
    echo "$MODE/$LEG: downloaded"
    ;;
  failed|expired|cancelled)
    echo "$MODE/$LEG: $status" >&2
    jq -r '.error.message // "task did not succeed"' "$WORK/status-response.json" >&2
    exit 5
    ;;
  *)
    echo "$MODE/$LEG: unknown status '$status'" >&2
    exit 6
    ;;
esac
