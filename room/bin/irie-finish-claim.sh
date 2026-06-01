#!/bin/bash
# irie-finish-claim.sh — claim済み画像のdescription保存+pending削除
# usage: irie-finish-claim.sh <file_id>
# stdin or $3 でdescriptionを渡す

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOM="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOM="${IRIE_ROOM:-${KAIGI_ROOM:-$DEFAULT_ROOM}}"
UPLOADS="${IRIE_UPLOADS:-${KAIGI_UPLOADS:-$ROOM/uploads}}"
API="${IRIE_DESCRIBE_API:-${KAIGI_DESCRIBE_API:-http://127.0.0.1:8901/api/describe}}"
FILE_ID="$1"
CLAIMER="$2"
DESC="$3"

if [ -z "$DESC" ]; then
  DESC=$(cat)
fi

if [ -z "$FILE_ID" ] || [ -z "$CLAIMER" ] || [ -z "$DESC" ]; then
  echo "usage: irie-finish-claim.sh <file_id> <claimer> <description>" >&2
  exit 2
fi

# API経由でmeta.jsonlに保存
if ! curl -fsS -X POST "$API" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json,sys; print(json.dumps({'id':sys.argv[1],'description':sys.argv[2],'claimed_by':sys.argv[3]}))" "$FILE_ID" "$DESC" "$CLAIMER")" \
  > /dev/null; then
  echo "DESCRIBE_FAILED" >&2
  exit 1
fi

# pending削除
rm -f "$UPLOADS/${FILE_ID}.pending" "$UPLOADS/${FILE_ID}.pending.lock"
echo "DONE"
