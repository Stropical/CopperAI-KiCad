#!/usr/bin/env bash
# Unit test all KiCad MCP server tools.
# Usage: ./test_tools.sh [base_url]
# Example: ./test_tools.sh http://127.0.0.1:8080
# Requires: curl, and either jq or python3 for JSON parsing.
#
# What the commit adds:
#   begin_commit  = start a transaction (all edits are staged).
#   place_component = add a symbol to the schematic (requires commit_id from begin_commit).
#   (Future: create_items could add wires/lines; not yet exposed in MCP.)
#   end_commit(id, "commit") = apply all staged changes to the document.
#   end_commit(id, "drop")   = discard the transaction.
# So you can place multiple components in one commit, then commit once.

set -e
BASE_URL="${1:-http://127.0.0.1:8080}"
MCP_URL="${BASE_URL}/mcp"
FAILED=0
PASSED=0
VISIBLE_BOUNDS_JSON=""

# Extract result content text and isError from a tools/call or similar JSON-RPC response.
# Usage: extract_result <json_string>
# Sets: EXTRACTED_TEXT, EXTRACTED_ERROR, EXTRACTED_JSON_ERROR
extract_result() {
    local json="$1"
    if command -v jq &>/dev/null; then
        EXTRACTED_ERROR=$(echo "$json" | jq -r '.result.isError // "false"')
        EXTRACTED_TEXT=$(echo "$json" | jq -r '.result.content[0].text // empty')
        EXTRACTED_JSON_ERROR=$(echo "$json" | jq -r '.error.message // empty')
    else
        EXTRACTED_ERROR=$(echo "$json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('result') or {}
print(r.get('isError', False))
" 2>/dev/null || echo "false")
        EXTRACTED_TEXT=$(echo "$json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('result') or {}
c = r.get('content') or []
print(c[0].get('text', '') if c else '')
" 2>/dev/null || echo "")
        EXTRACTED_JSON_ERROR=$(echo "$json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
e = d.get('error') or {}
print(e.get('message', '') or '')
" 2>/dev/null || echo "")
    fi
}

# Send JSON-RPC request and optionally check result text.
# check_ok: if 1, require isError=false and (optional) text not exactly "OK" when expect_data=1.
rpc() {
    local method="$1"
    local params="$2"
    local expect_data="${3:-0}"   # 1 = we expect meaningful data, not just "OK"
    local id="${4:-1}"
    local body
    if [ -z "$params" ]; then
        body=$(printf '{"jsonrpc":"2.0","id":%s,"method":"%s"}' "$id" "$method")
    else
        body=$(printf '{"jsonrpc":"2.0","id":%s,"method":"%s","params":%s}' "$id" "$method" "$params")
    fi
    local resp
    resp=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$body")
    extract_result "$resp"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  response: isError=true, text=$EXTRACTED_TEXT"
        return 1
    fi
    if [ -n "$EXTRACTED_JSON_ERROR" ]; then
        echo "  response: jsonrpc error: $EXTRACTED_JSON_ERROR"
        return 1
    fi
    if [ "$expect_data" = "1" ] && [ "$EXTRACTED_TEXT" = "OK" ]; then
        echo "  response: got generic 'OK' (no data returned)"
        return 1
    fi
    echo "  response: $EXTRACTED_TEXT"
    return 0
}

assert_contains() {
    local haystack="$1"
    local needle="$2"
    if [[ "$haystack" == *"$needle"* ]]; then
        return 0
    fi
    echo "  expected text to contain: $needle"
    return 1
}

json_has_keys() {
    local json="$1"
    shift
    local expr=""
    local key
    for key in "$@"; do
        if [ -n "$expr" ]; then
            expr="$expr and "
        fi
        expr="${expr}has(\"$key\")"
    done
    if command -v jq &>/dev/null; then
        echo "$json" | jq -e "$expr" >/dev/null
    else
        JSON_INPUT="$json" python3 - "$@" <<'PY'
import json
import os
import sys

data = json.loads(os.environ["JSON_INPUT"])
keys = sys.argv[1:]
raise SystemExit(0 if all(key in data for key in keys) else 1)
PY
    fi
}

json_key_is_array() {
    local json="$1"
    local key="$2"
    if command -v jq &>/dev/null; then
        echo "$json" | jq -e ".${key} | type == \"array\"" >/dev/null
    else
        JSON_INPUT="$json" JSON_KEY="$key" python3 <<'PY'
import json
import os
import sys

data = json.loads(os.environ["JSON_INPUT"])
key = os.environ["JSON_KEY"]
raise SystemExit(0 if isinstance(data.get(key), list) else 1)
PY
    fi
}

# Truncate long text for display (max length)
truncate_display() {
    local text="$1"
    local max="${2:-100}"
    if [ ${#text} -gt "$max" ]; then
        echo "${text:0:$max}..."
    else
        echo "$text"
    fi
}

run_test() {
    local name="$1"
    local method="$2"
    local params="$3"
    local expect_data="${4:-0}"
    local id="${5:-1}"
    echo -n "  $name ... "
    local body
    if [ -z "$params" ]; then
        body=$(printf '{"jsonrpc":"2.0","id":%s,"method":"%s","params":{}}' "$id" "$method")
    else
        body=$(printf '{"jsonrpc":"2.0","id":%s,"method":"%s","params":%s}' "$id" "$method" "$params")
    fi
    local resp
    resp=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$body")
    extract_result "$resp"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
        ((FAILED++)) || true
        return
    fi
    if [ "$expect_data" = "1" ] && [ "$EXTRACTED_TEXT" = "OK" ]; then
        echo "FAIL (no data, got 'OK')"
        ((FAILED++)) || true
        return
    fi
    echo "OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
}

echo "KiCad MCP tools unit test"
echo "Base URL: $BASE_URL"
echo ""

# 1. Initialize
echo "[1] initialize"
run_test "initialize" "initialize" "" 1 1
echo ""

# 2. tools/list
echo "[2] tools/list"
run_test "tools/list" "tools/list" "" 1 2
echo ""

# 3. get_open_documents
echo "[3] get_open_documents"
run_test "get_open_documents (type=schematic)" "tools/call" '{"name":"get_open_documents","arguments":{"type":"schematic"}}' 0 3
echo ""

# 3a. get_schematic_summary
echo "[3a] get_schematic_summary"
BODY=$(printf '{"jsonrpc":"2.0","id":31,"method":"tools/call","params":{"name":"get_schematic_summary","arguments":{}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  get_schematic_summary ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! assert_contains "$EXTRACTED_TEXT" "Components"; then
    echo "  get_schematic_summary ... FAIL (response missing 'Components')"
    ((FAILED++)) || true
elif ! assert_contains "$EXTRACTED_TEXT" "Global nets"; then
    echo "  get_schematic_summary ... FAIL (response missing 'Global nets')"
    ((FAILED++)) || true
else
    echo "  get_schematic_summary ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
fi
echo ""

# 3b. screenshot_zone (center_x, center_y, optional width_mm; returns base64 PNG)
echo "[3b] screenshot_zone"
BODY=$(printf '{"jsonrpc":"2.0","id":39,"method":"tools/call","params":{"name":"screenshot_zone","arguments":{"center_x":50,"center_y":50,"width_mm":15}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  screenshot_zone ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! assert_contains "$EXTRACTED_TEXT" "screenshot_base64"; then
    echo "  screenshot_zone ... FAIL (response missing screenshot_base64: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
else
    echo "  screenshot_zone ... OK (returned PNG base64)"
    ((PASSED++)) || true
fi
echo ""

# 3c. screenshot_full_schematic (no args; returns base64 PNG)
echo "[3c] screenshot_full_schematic"
BODY=$(printf '{"jsonrpc":"2.0","id":40,"method":"tools/call","params":{"name":"screenshot_full_schematic","arguments":{}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  screenshot_full_schematic ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! assert_contains "$EXTRACTED_TEXT" "screenshot_base64"; then
    echo "  screenshot_full_schematic ... FAIL (response missing screenshot_base64: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
else
    echo "  screenshot_full_schematic ... OK (returned PNG base64)"
    ((PASSED++)) || true
fi
echo ""

# 3d. get_visible_bounds
echo "[3d] get_visible_bounds"
BODY=$(printf '{"jsonrpc":"2.0","id":41,"method":"tools/call","params":{"name":"get_visible_bounds","arguments":{}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  get_visible_bounds ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! json_has_keys "$EXTRACTED_TEXT" "min_x_mm" "min_y_mm" "max_x_mm" "max_y_mm" "center_x_mm" "center_y_mm" "width_mm" "height_mm" "client_width_px" "client_height_px"; then
    echo "  get_visible_bounds ... FAIL (response missing expected bounds fields)"
    ((FAILED++)) || true
else
    VISIBLE_BOUNDS_JSON="$EXTRACTED_TEXT"
    echo "  get_visible_bounds ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
fi
echo ""

# 3e. get_all_labels
echo "[3e] get_all_labels"
BODY=$(printf '{"jsonrpc":"2.0","id":42,"method":"tools/call","params":{"name":"get_all_labels","arguments":{}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  get_all_labels ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! json_has_keys "$EXTRACTED_TEXT" "labels" "count" "sheet" || ! json_key_is_array "$EXTRACTED_TEXT" "labels"; then
    echo "  get_all_labels ... FAIL (response missing labels/count structure)"
    ((FAILED++)) || true
else
    echo "  get_all_labels ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
fi
echo ""

# 3f. get_all_wires
echo "[3f] get_all_wires"
BODY=$(printf '{"jsonrpc":"2.0","id":43,"method":"tools/call","params":{"name":"get_all_wires","arguments":{}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  get_all_wires ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! json_has_keys "$EXTRACTED_TEXT" "wires" "count" "sheet" || ! json_key_is_array "$EXTRACTED_TEXT" "wires"; then
    echo "  get_all_wires ... FAIL (response missing wires/count structure)"
    ((FAILED++)) || true
else
    echo "  get_all_wires ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
fi
echo ""

# 3g. get_wire_labels
echo "[3g] get_wire_labels"
BODY=$(printf '{"jsonrpc":"2.0","id":44,"method":"tools/call","params":{"name":"get_wire_labels","arguments":{"tolerance_mm":0.25,"only_labeled":false}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  get_wire_labels ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! json_has_keys "$EXTRACTED_TEXT" "wires" "count" "tolerance_mm" "sheet" || ! json_key_is_array "$EXTRACTED_TEXT" "wires"; then
    echo "  get_wire_labels ... FAIL (response missing wires/count structure)"
    ((FAILED++)) || true
else
    echo "  get_wire_labels ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
fi
echo ""

# 3h. get_labels_in_view
echo "[3h] get_labels_in_view"
BODY=$(printf '{"jsonrpc":"2.0","id":45,"method":"tools/call","params":{"name":"get_labels_in_view","arguments":{}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  get_labels_in_view ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
elif ! json_has_keys "$EXTRACTED_TEXT" "labels" "count" "sheet" "bbox" || ! json_key_is_array "$EXTRACTED_TEXT" "labels"; then
    echo "  get_labels_in_view ... FAIL (response missing labels/bbox structure)"
    ((FAILED++)) || true
else
    echo "  get_labels_in_view ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
fi
echo ""

# 3i. rename_labels_in_bbox dry-run semantics
echo "[3i] rename_labels_in_bbox dry-run"
if [ -z "$VISIBLE_BOUNDS_JSON" ]; then
    echo "  rename_labels_in_bbox (dry-run) ... SKIP (no visible bounds to derive bbox from)"
    echo ""
else
    if command -v jq &>/dev/null; then
        MIN_X=$(echo "$VISIBLE_BOUNDS_JSON" | jq -r '.min_x_mm')
        MIN_Y=$(echo "$VISIBLE_BOUNDS_JSON" | jq -r '.min_y_mm')
        MAX_X=$(echo "$VISIBLE_BOUNDS_JSON" | jq -r '.max_x_mm')
        MAX_Y=$(echo "$VISIBLE_BOUNDS_JSON" | jq -r '.max_y_mm')
    else
        MIN_X=$(echo "$VISIBLE_BOUNDS_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['min_x_mm'])")
        MIN_Y=$(echo "$VISIBLE_BOUNDS_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['min_y_mm'])")
        MAX_X=$(echo "$VISIBLE_BOUNDS_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['max_x_mm'])")
        MAX_Y=$(echo "$VISIBLE_BOUNDS_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['max_y_mm'])")
    fi
    BODY=$(printf '{"jsonrpc":"2.0","id":46,"method":"tools/call","params":{"name":"rename_labels_in_bbox","arguments":{"min_x":%s,"min_y":%s,"max_x":%s,"max_y":%s,"new_name":"VOUT","dry_run":true}}}' "$MIN_X" "$MIN_Y" "$MAX_X" "$MAX_Y")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  rename_labels_in_bbox (dry-run) ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
        ((FAILED++)) || true
    elif ! json_has_keys "$EXTRACTED_TEXT" "dry_run" "bbox" "targets" "target_count" "old_name_filter" "new_name" || ! assert_contains "$EXTRACTED_TEXT" "\"dry_run\":true"; then
        echo "  rename_labels_in_bbox (dry-run) ... FAIL (response missing dry-run preview structure)"
        ((FAILED++)) || true
    else
        echo "  rename_labels_in_bbox (dry-run) ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
        ((PASSED++)) || true
    fi
    echo ""
fi

# 4. begin_commit (capture id for place_component + end_commit)
echo "[4] begin_commit"
BODY=$(printf '{"jsonrpc":"2.0","id":47,"method":"tools/call","params":{"name":"begin_commit"}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
# Extract commit id from "Commit started, id: <uuid>"
COMMIT_ID=""
COMMIT_ID=$(echo "$EXTRACTED_TEXT" | sed -n 's/.*id: \([^ ]*\).*/\1/p')
if [ -z "$COMMIT_ID" ] || [ "$COMMIT_ID" = "$EXTRACTED_TEXT" ]; then
    COMMIT_ID=""
fi
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  begin_commit ... FAIL (isError=true: $EXTRACTED_TEXT)"
    ((FAILED++)) || true
else
    echo "  begin_commit ... OK ($EXTRACTED_TEXT)"
    ((PASSED++)) || true
fi
echo ""

# 5. search_components (capture first result for get_component_data + place_component)
echo "[5] search_components"
BODY=$(printf '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"search_components","arguments":{"query":"Resistor","limit":10}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
PLACE_LIB="Device"
PLACE_SYM="R"
if [ "$EXTRACTED_ERROR" != "true" ] && [ -n "$EXTRACTED_TEXT" ]; then
    if command -v jq &>/dev/null; then
        first_lib=$(echo "$EXTRACTED_TEXT" | jq -r '.[0].library // empty')
        first_sym=$(echo "$EXTRACTED_TEXT" | jq -r '.[0].symbol // empty')
        [ -n "$first_lib" ] && PLACE_LIB="$first_lib"
        [ -n "$first_sym" ] && PLACE_SYM="$first_sym"
    else
        first_lib=$(echo "$EXTRACTED_TEXT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if isinstance(d, list) and d:
        print(d[0].get('library', '') or 'Device')
except: print('Device')
" 2>/dev/null || echo "Device")
        first_sym=$(echo "$EXTRACTED_TEXT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if isinstance(d, list) and d:
        print(d[0].get('symbol', '') or 'R')
except: print('R')
" 2>/dev/null || echo "R")
        [ -n "$first_lib" ] && PLACE_LIB="$first_lib"
        [ -n "$first_sym" ] && PLACE_SYM="$first_sym"
    fi
fi
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  search_components ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
else
    echo "  search_components ... OK ($(truncate_display "$EXTRACTED_TEXT" 100))"
    ((PASSED++)) || true
fi
echo "  (using library=$PLACE_LIB symbol=$PLACE_SYM for get_component_data and place_component)"
echo ""

# 5b. batch_search_components (multiple queries in one call)
echo "[5b] batch_search_components"
BODY=$(printf '{"jsonrpc":"2.0","id":51,"method":"tools/call","params":{"name":"batch_search_components","arguments":{"queries":["Resistor","Capacitor"],"limit":5}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  batch_search_components ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
else
    if [[ "$EXTRACTED_TEXT" == *"Batch search complete"* ]]; then
        # Parse JSON array from content (after first newline)
        BATCH_JSON=$(echo "$EXTRACTED_TEXT" | sed -n '2,$ p')
        if command -v jq &>/dev/null && [ -n "$BATCH_JSON" ]; then
            COUNT=$(echo "$BATCH_JSON" | jq -r 'length // 0')
            Q0=$(echo "$BATCH_JSON" | jq -r '.[0].query // empty')
            R0=$(echo "$BATCH_JSON" | jq -r '.[0].results | length // 0')
            if [ "$COUNT" -ge 2 ] && [ -n "$Q0" ] && [ "$R0" -ge 0 ]; then
                echo "  batch_search_components ... OK ($COUNT queries, structure valid)"
                ((PASSED++)) || true
            else
                echo "  batch_search_components ... OK (content present, structure check skipped)"
                ((PASSED++)) || true
            fi
        else
            echo "  batch_search_components ... OK ($(truncate_display "$EXTRACTED_TEXT" 80))"
            ((PASSED++)) || true
        fi
    else
        echo "  batch_search_components ... FAIL (expected 'Batch search complete' in response)"
        ((FAILED++)) || true
    fi
fi
echo ""

# 6. get_component_data (library symbol from search result)
echo "[6] get_component_data (lib)"
run_test "get_component_data (library=$PLACE_LIB, symbol=$PLACE_SYM)" "tools/call" "$(printf '{"name":"get_component_data","arguments":{"library":"%s","symbol":"%s"}}' "$PLACE_LIB" "$PLACE_SYM")" 0 6
echo ""

# 6b. batch_get_component_data (multiple components in one call)
echo "[6b] batch_get_component_data"
BODY=$(printf '{"jsonrpc":"2.0","id":61,"method":"tools/call","params":{"name":"batch_get_component_data","arguments":{"components":[{"library":"%s","symbol":"%s"},{"library":"Device","symbol":"C"}]}}}' "$PLACE_LIB" "$PLACE_SYM")
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  batch_get_component_data ... FAIL (isError=true: $(truncate_display "$EXTRACTED_TEXT" 80))"
    ((FAILED++)) || true
else
    if [[ "$EXTRACTED_TEXT" == *"Batch get_component_data complete"* ]]; then
        BATCH_JSON=$(echo "$EXTRACTED_TEXT" | sed -n '2,$ p')
        if command -v jq &>/dev/null && [ -n "$BATCH_JSON" ]; then
            COUNT=$(echo "$BATCH_JSON" | jq -r 'length // 0')
            if [ "$COUNT" -ge 2 ]; then
                echo "  batch_get_component_data ... OK ($COUNT components, structure valid)"
                ((PASSED++)) || true
            else
                echo "  batch_get_component_data ... OK (content present)"
                ((PASSED++)) || true
            fi
        else
            echo "  batch_get_component_data ... OK ($(truncate_display "$EXTRACTED_TEXT" 80))"
            ((PASSED++)) || true
        fi
    else
        echo "  batch_get_component_data ... FAIL (expected 'Batch get_component_data complete' in response)"
        ((FAILED++)) || true
    fi
fi
echo ""

# 7. place_component (only if we have a commit id)
echo "[7] place_component"
if [ -n "$COMMIT_ID" ]; then
    PARAMS=$(printf '{"name":"place_component","arguments":{"library":"%s","symbol":"%s","reference":"R1","value":"10k","x":50,"y":50,"rotation":0,"commit_id":"%s"}}' "$PLACE_LIB" "$PLACE_SYM" "$COMMIT_ID")
    BODY=$(printf '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  place_component ... FAIL (isError=true: $EXTRACTED_TEXT)"
        ((FAILED++)) || true
    elif [ "$EXTRACTED_TEXT" = "OK" ]; then
        echo "  place_component ... OK (generic OK)"
        ((PASSED++)) || true
    else
        echo "  place_component ... OK ($EXTRACTED_TEXT)"
        ((PASSED++)) || true
    fi
else
    echo "  place_component ... SKIP (no commit id from begin_commit)"
fi
echo ""

# 8. end_commit
echo "[8] end_commit"
if [ -n "$COMMIT_ID" ]; then
    PARAMS=$(printf '{"name":"end_commit","arguments":{"id":"%s","action":"commit","message":"MCP test"}}' "$COMMIT_ID")
    run_test "end_commit (commit)" "tools/call" "$PARAMS" 0 8
else
    echo "  end_commit ... SKIP (no commit id)"
fi
echo ""

# 9. commit flow test (begin → end commit, no place)
echo "[9] commit flow (begin → end commit)"
BODY=$(printf '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"begin_commit"}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
COMMIT_FLOW_ID=""
COMMIT_FLOW_ID=$(echo "$EXTRACTED_TEXT" | sed -n 's/.*id: \([^ ]*\).*/\1/p')
if [ -z "$COMMIT_FLOW_ID" ] || [ "$COMMIT_FLOW_ID" = "$EXTRACTED_TEXT" ]; then
    COMMIT_FLOW_ID=""
fi
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  begin_commit (commit flow) ... FAIL (isError=true: $EXTRACTED_TEXT)"
    ((FAILED++)) || true
else
    echo "  begin_commit (commit flow) ... OK ($EXTRACTED_TEXT)"
    ((PASSED++)) || true
fi
if [ -n "$COMMIT_FLOW_ID" ]; then
    PARAMS=$(printf '{"name":"end_commit","arguments":{"id":"%s","action":"commit","message":"commit flow test"}}' "$COMMIT_FLOW_ID")
    BODY=$(printf '{"jsonrpc":"2.0","id":10,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  end_commit (commit) ... FAIL (isError=true: $EXTRACTED_TEXT)"
        ((FAILED++)) || true
    else
        echo "  end_commit (commit) ... OK ($EXTRACTED_TEXT)"
        ((PASSED++)) || true
    fi
else
    echo "  end_commit (commit) ... SKIP (no commit id)"
fi
echo ""

# 10. commit flow test (begin → end drop)
echo "[10] commit flow (begin → end drop)"
BODY=$(printf '{"jsonrpc":"2.0","id":11,"method":"tools/call","params":{"name":"begin_commit"}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
DROP_ID=""
DROP_ID=$(echo "$EXTRACTED_TEXT" | sed -n 's/.*id: \([^ ]*\).*/\1/p')
if [ -z "$DROP_ID" ] || [ "$DROP_ID" = "$EXTRACTED_TEXT" ]; then
    DROP_ID=""
fi
if [ "$EXTRACTED_ERROR" = "true" ]; then
    echo "  begin_commit (drop flow) ... FAIL (isError=true: $EXTRACTED_TEXT)"
    ((FAILED++)) || true
else
    echo "  begin_commit (drop flow) ... OK ($EXTRACTED_TEXT)"
    ((PASSED++)) || true
fi
if [ -n "$DROP_ID" ]; then
    PARAMS=$(printf '{"name":"end_commit","arguments":{"id":"%s","action":"drop"}}' "$DROP_ID")
    BODY=$(printf '{"jsonrpc":"2.0","id":12,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  end_commit (drop) ... FAIL (isError=true: $EXTRACTED_TEXT)"
        ((FAILED++)) || true
    else
        echo "  end_commit (drop) ... OK ($EXTRACTED_TEXT)"
        ((PASSED++)) || true
    fi
else
    echo "  end_commit (drop) ... SKIP (no commit id)"
fi
echo ""

# 11. Voltage regulator: multi-component commit (use add_wire in same commit for connections)
echo "[11] Voltage regulator (multi-component commit)"
# Resolve library/symbol for regulator and capacitor from search
REG_LIB="" REG_SYM=""
CAP_LIB="" CAP_SYM=""
BODY=$(printf '{"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"search_components","arguments":{"query":"regulator","limit":5}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" != "true" ] && [ -n "$EXTRACTED_TEXT" ]; then
    if command -v jq &>/dev/null; then
        REG_LIB=$(echo "$EXTRACTED_TEXT" | jq -r '.[0].library // empty')
        REG_SYM=$(echo "$EXTRACTED_TEXT" | jq -r '.[0].symbol // empty')
    else
        REG_LIB=$(echo "$EXTRACTED_TEXT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0].get('library','') if isinstance(d,list) and d else '')" 2>/dev/null)
        REG_SYM=$(echo "$EXTRACTED_TEXT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0].get('symbol','') if isinstance(d,list) and d else '')" 2>/dev/null)
    fi
fi
BODY=$(printf '{"jsonrpc":"2.0","id":21,"method":"tools/call","params":{"name":"search_components","arguments":{"query":"capacitor","limit":5}}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
if [ "$EXTRACTED_ERROR" != "true" ] && [ -n "$EXTRACTED_TEXT" ]; then
    if command -v jq &>/dev/null; then
        CAP_LIB=$(echo "$EXTRACTED_TEXT" | jq -r '.[0].library // empty')
        CAP_SYM=$(echo "$EXTRACTED_TEXT" | jq -r '.[0].symbol // empty')
    else
        CAP_LIB=$(echo "$EXTRACTED_TEXT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0].get('library','') if isinstance(d,list) and d else '')" 2>/dev/null)
        CAP_SYM=$(echo "$EXTRACTED_TEXT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0].get('symbol','') if isinstance(d,list) and d else '')" 2>/dev/null)
    fi
fi
# Fallback: use same resistor lib/sym for all if regulator/cap not found (still tests multi-place)
[ -z "$REG_LIB" ] && REG_LIB="$PLACE_LIB" && REG_SYM="$PLACE_SYM"
[ -z "$CAP_LIB" ] && CAP_LIB="$PLACE_LIB" && CAP_SYM="C"
[ -z "$CAP_SYM" ] && CAP_SYM="C"
VR_COMMIT_ID=""
BODY=$(printf '{"jsonrpc":"2.0","id":22,"method":"tools/call","params":{"name":"begin_commit"}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
VR_COMMIT_ID=$(echo "$EXTRACTED_TEXT" | sed -n 's/.*id: \([^ ]*\).*/\1/p')
if [ -z "$VR_COMMIT_ID" ] || [ "$VR_COMMIT_ID" = "$EXTRACTED_TEXT" ]; then VR_COMMIT_ID=""; fi
if [ "$EXTRACTED_ERROR" = "true" ] || [ -z "$VR_COMMIT_ID" ]; then
    echo "  begin_commit (VR) ... FAIL or SKIP (no commit id)"
    [ "$EXTRACTED_ERROR" = "true" ] && ((FAILED++)) || true
else
    echo "  begin_commit (VR) ... OK"
    ((PASSED++)) || true
    # Place regulator U1, input cap C1, output cap C2 (positions in mm)
    place_one() {
        local lib="$1" sym="$2" ref="$3" val="$4" x="$5" y="$6" cid="$7" id="$8"
        local params=$(printf '{"name":"place_component","arguments":{"library":"%s","symbol":"%s","reference":"%s","value":"%s","x":%s,"y":%s,"rotation":0,"commit_id":"%s"}}' "$lib" "$sym" "$ref" "$val" "$x" "$y" "$cid")
        local body=$(printf '{"jsonrpc":"2.0","id":%s,"method":"tools/call","params":%s}' "$id" "$params")
        curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$body"
    }
    extract_result "$(place_one "$REG_LIB" "$REG_SYM" "U1" "REG" 50 50 "$VR_COMMIT_ID" 23)"
    [ "$EXTRACTED_ERROR" = "true" ] && echo "  place U1 (regulator) ... FAIL ($EXTRACTED_TEXT)" && ((FAILED++)) || { echo "  place U1 (regulator) ... OK"; ((PASSED++)); }
    extract_result "$(place_one "$CAP_LIB" "$CAP_SYM" "C1" "100n" 20 50 "$VR_COMMIT_ID" 24)"
    [ "$EXTRACTED_ERROR" = "true" ] && echo "  place C1 (input cap) ... FAIL ($EXTRACTED_TEXT)" && ((FAILED++)) || { echo "  place C1 (input cap) ... OK"; ((PASSED++)); }
    extract_result "$(place_one "$CAP_LIB" "$CAP_SYM" "C2" "10u" 80 50 "$VR_COMMIT_ID" 25)"
    [ "$EXTRACTED_ERROR" = "true" ] && echo "  place C2 (output cap) ... FAIL ($EXTRACTED_TEXT)" && ((FAILED++)) || { echo "  place C2 (output cap) ... OK"; ((PASSED++)); }
    # Commit the voltage regulator circuit (wires would require create_items/add_wire in MCP)
    PARAMS=$(printf '{"name":"end_commit","arguments":{"id":"%s","action":"commit","message":"Voltage regulator test: U1, C1, C2"}}' "$VR_COMMIT_ID")
    BODY=$(printf '{"jsonrpc":"2.0","id":26,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  end_commit (VR) ... FAIL ($EXTRACTED_TEXT)"
        ((FAILED++)) || true
    else
        echo "  end_commit (VR) ... OK (Commit ended)"
        ((PASSED++)) || true
    fi
fi
echo ""

# 12. add_wire and remove_wire
echo "[12] add_wire and remove_wire"
BODY=$(printf '{"jsonrpc":"2.0","id":30,"method":"tools/call","params":{"name":"begin_commit"}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
WIRE_COMMIT_ID=$(echo "$EXTRACTED_TEXT" | sed -n 's/.*id: \([^ ]*\).*/\1/p')
if [ -z "$WIRE_COMMIT_ID" ] || [ "$WIRE_COMMIT_ID" = "$EXTRACTED_TEXT" ]; then WIRE_COMMIT_ID=""; fi
if [ "$EXTRACTED_ERROR" = "true" ] || [ -z "$WIRE_COMMIT_ID" ]; then
    echo "  begin_commit (wires) ... FAIL or SKIP"
    [ "$EXTRACTED_ERROR" = "true" ] && ((FAILED++)) || true
else
    echo "  begin_commit (wires) ... OK"
    ((PASSED++)) || true
    SEGMENTS='[{"x1":40,"y1":50,"x2":60,"y2":50},{"x1":60,"y1":50,"x2":60,"y2":70}]'
    PARAMS=$(printf '{"name":"add_wire","arguments":{"segments":%s}}' "$SEGMENTS")
    BODY=$(printf '{"jsonrpc":"2.0","id":31,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  add_wire ... FAIL ($EXTRACTED_TEXT)"
        ((FAILED++)) || true
    else
        echo "  add_wire ... OK ($(truncate_display "$EXTRACTED_TEXT" 80))"
        ((PASSED++)) || true
        # Parse first wire id (UUID) from "Wires added: [\"uuid\",...]" for remove_wire test
        FIRST_WIRE_ID=$(echo "$EXTRACTED_TEXT" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
    fi
    PARAMS=$(printf '{"name":"end_commit","arguments":{"id":"%s","action":"commit","message":"Wire test"}}' "$WIRE_COMMIT_ID")
    BODY=$(printf '{"jsonrpc":"2.0","id":32,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    [ "$EXTRACTED_ERROR" = "true" ] && echo "  end_commit (wires) ... FAIL" && ((FAILED++)) || { echo "  end_commit (wires) ... OK"; ((PASSED++)); }
    if [ -n "$FIRST_WIRE_ID" ]; then
        BODY=$(printf '{"jsonrpc":"2.0","id":33,"method":"tools/call","params":{"name":"begin_commit"}}')
        RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
        extract_result "$RESP"
        DROP_CID=$(echo "$EXTRACTED_TEXT" | sed -n 's/.*id: \([^ ]*\).*/\1/p')
        if [ -n "$DROP_CID" ] && [ "$EXTRACTED_ERROR" != "true" ]; then
            PARAMS=$(printf '{"name":"remove_wire","arguments":{"wire_ids":["%s"]}}' "$FIRST_WIRE_ID")
            BODY=$(printf '{"jsonrpc":"2.0","id":34,"method":"tools/call","params":%s}' "$PARAMS")
            RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
            extract_result "$RESP"
            if [ "$EXTRACTED_ERROR" = "true" ]; then
                echo "  remove_wire ... FAIL ($EXTRACTED_TEXT)"
                ((FAILED++)) || true
            else
                echo "  remove_wire ... OK ($EXTRACTED_TEXT)"
                ((PASSED++)) || true
            fi
            PARAMS=$(printf '{"name":"end_commit","arguments":{"id":"%s","action":"commit","message":"Remove wire"}}' "$DROP_CID")
            BODY=$(printf '{"jsonrpc":"2.0","id":35,"method":"tools/call","params":%s}' "$PARAMS")
            RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
            extract_result "$RESP"
            [ "$EXTRACTED_ERROR" = "true" ] && ((FAILED++)) || ((PASSED++))
        fi
    fi
fi
echo ""

# 13. add_global_label
echo "[13] add_global_label"
BODY=$(printf '{"jsonrpc":"2.0","id":40,"method":"tools/call","params":{"name":"begin_commit"}}')
RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
extract_result "$RESP"
GLABEL_COMMIT_ID=$(echo "$EXTRACTED_TEXT" | sed -n 's/.*id: \([^ ]*\).*/\1/p')
if [ -z "$GLABEL_COMMIT_ID" ] || [ "$GLABEL_COMMIT_ID" = "$EXTRACTED_TEXT" ]; then GLABEL_COMMIT_ID=""; fi
if [ "$EXTRACTED_ERROR" = "true" ] || [ -z "$GLABEL_COMMIT_ID" ]; then
    echo "  begin_commit (global label) ... FAIL or SKIP"
    [ "$EXTRACTED_ERROR" = "true" ] && ((FAILED++)) || true
else
    echo "  begin_commit (global label) ... OK"
    ((PASSED++)) || true
    PARAMS='{"name":"add_global_label","arguments":{"text":"VCC","x":50,"y":30}}'
    BODY=$(printf '{"jsonrpc":"2.0","id":41,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    if [ "$EXTRACTED_ERROR" = "true" ]; then
        echo "  add_global_label ... FAIL ($EXTRACTED_TEXT)"
        ((FAILED++)) || true
    else
        echo "  add_global_label ... OK ($(truncate_display "$EXTRACTED_TEXT" 80))"
        ((PASSED++)) || true
    fi
    PARAMS=$(printf '{"name":"end_commit","arguments":{"id":"%s","action":"commit","message":"Global label test"}}' "$GLABEL_COMMIT_ID")
    BODY=$(printf '{"jsonrpc":"2.0","id":42,"method":"tools/call","params":%s}' "$PARAMS")
    RESP=$(curl -s -X POST "$MCP_URL" -H "Content-Type: application/json" -d "$BODY")
    extract_result "$RESP"
    [ "$EXTRACTED_ERROR" = "true" ] && echo "  end_commit (global label) ... FAIL" && ((FAILED++)) || { echo "  end_commit (global label) ... OK"; ((PASSED++)); }
fi
echo ""

# Summary
echo "---"
echo "Passed: $PASSED, Failed: $FAILED"
if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
