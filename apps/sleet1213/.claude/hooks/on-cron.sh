#!/bin/bash
# PostToolUse hook for CronCreate and CronDelete.
# Maintains /tmp/sleet1213-crons.json with the current cron job list.

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
TOOL_INPUT=$(echo "$INPUT" | jq -c '.tool_input // {}' 2>/dev/null)
TOOL_RESPONSE=$(echo "$INPUT" | jq -r '.tool_response // empty' 2>/dev/null)

CRON_FILE="/tmp/sleet1213-crons.json"

if [ ! -f "$CRON_FILE" ]; then
  echo '{"updated":"","crons":[]}' > "$CRON_FILE"
fi

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

case "$TOOL_NAME" in
  CronCreate)
    CRON_EXPR=$(echo "$TOOL_INPUT" | jq -r '.cron // "?"')
    PROMPT=$(echo "$TOOL_INPUT" | jq -r '.prompt // "?"')
    RECURRING=$(echo "$TOOL_INPUT" | jq -r '.recurring // true')
    DURABLE=$(echo "$TOOL_INPUT" | jq -r '.durable // false')
    JOB_ID=$(echo "$TOOL_RESPONSE" | grep -oP '(?<=job )[a-f0-9]+' | head -1)
    [ -z "$JOB_ID" ] && JOB_ID="unknown"
    SHORT_PROMPT=$(echo "$PROMPT" | head -c 60)
    jq -c --arg id "$JOB_ID" --arg cron "$CRON_EXPR" --arg prompt "$SHORT_PROMPT" \
       --arg recurring "$RECURRING" --arg durable "$DURABLE" --arg now "$NOW" \
      '.updated = $now | .crons += [{id: $id, cron: $cron, prompt: $prompt, recurring: ($recurring == "true"), durable: ($durable == "true"), state: "scheduled"}]' \
      "$CRON_FILE" > "${CRON_FILE}.tmp" && mv "${CRON_FILE}.tmp" "$CRON_FILE"
    ;;
  CronDelete)
    DEL_ID=$(echo "$TOOL_INPUT" | jq -r '.id // empty')
    if [ -n "$DEL_ID" ]; then
      jq -c --arg id "$DEL_ID" --arg now "$NOW" \
        '.updated = $now | .crons = [.crons[] | select(.id != $id)]' \
        "$CRON_FILE" > "${CRON_FILE}.tmp" && mv "${CRON_FILE}.tmp" "$CRON_FILE"
    fi
    ;;
  CronList)
    jq -c --arg now "$NOW" '.updated = $now' \
      "$CRON_FILE" > "${CRON_FILE}.tmp" && mv "${CRON_FILE}.tmp" "$CRON_FILE"
    ;;
esac

exit 0
