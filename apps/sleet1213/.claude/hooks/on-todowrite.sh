#!/bin/bash
# PostToolUse hook for TodoWrite — writes the todo list to a JSON file
# that the Meteor TodoHud reads every 2 seconds.
#
# stdin: {"session_id":"...","tool_name":"TodoWrite","tool_input":{...},"tool_response":{...}}

INPUT=$(cat)
TOOL_INPUT=$(echo "$INPUT" | jq -c '.tool_input // empty' 2>/dev/null)

if [ -z "$TOOL_INPUT" ]; then
  exit 0
fi

# Write the todo list to the shared file for the HUD
echo "$TOOL_INPUT" | jq -c '{
  updated: (now | todate),
  todos: [.todos[] | {content, status, activeForm}]
}' > /tmp/sleet1213-todos.json 2>/dev/null

exit 0
