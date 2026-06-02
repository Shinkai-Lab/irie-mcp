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
# 値は環境変数で渡し、Pythonソースはシングルクォートにする。
# （macOSの bash 3.2 では "$(... "..." ...)" 内のdictリテラル {..} がブレース展開で
#  壊れるため。シングルクォートなら全bashで展開されず、特殊文字/改行も安全。）
PAYLOAD=$(IRIE_FID="$FILE_ID" IRIE_DSC="$DESC" IRIE_CLM="$CLAIMER" python3 -c 'import json, os
print(json.dumps({"id": os.environ["IRIE_FID"], "description": os.environ["IRIE_DSC"], "claimed_by": os.environ["IRIE_CLM"]}))')
if ! curl -fsS -X POST "$API" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  > /dev/null; then
  echo "DESCRIBE_FAILED" >&2
  exit 1
fi

# pending削除（Web UIは ${file_id}${ext}.pending で作るので glob で消す）
rm -f "$UPLOADS/${FILE_ID}"*.pending "$UPLOADS/${FILE_ID}"*.pending.lock
rmdir "$UPLOADS/${FILE_ID}"*.pending.lockd 2>/dev/null || true
echo "DONE"
