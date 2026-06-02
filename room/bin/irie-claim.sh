#!/bin/bash
# irie-claim.sh — 画像のdescription書き込み権を排他的に取得する
# usage: irie-claim.sh <file_id> <claimer_name>
# exit 0 + "CLAIMED" = 取得成功、画像を読んでdescを書いてよい
# exit 0 + "TAKEN_BY <name>" = 他の人が取得済み、読むな
# exit 1 = pendingファイルが存在しない

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOM="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOM="${IRIE_ROOM:-${KAIGI_ROOM:-$DEFAULT_ROOM}}"
UPLOADS="${IRIE_UPLOADS:-${KAIGI_UPLOADS:-$ROOM/uploads}}"
FILE_ID="$1"
CLAIMER="$2"

if [ -z "$FILE_ID" ] || [ -z "$CLAIMER" ]; then
  echo "usage: irie-claim.sh <file_id> <claimer_name>" >&2
  exit 2
fi

# pendingファイルはWeb UIが ${file_id}${ext}.pending で作るのでglobで探す
PENDING=$(find "$UPLOADS" -maxdepth 1 -name "${FILE_ID}*.pending" ! -name "*.lock" 2>/dev/null | head -1)

if [ -z "$PENDING" ] || [ ! -f "$PENDING" ]; then
  echo "NO_PENDING"
  exit 1
fi

# flock排他: pendingファイル自体をロック対象にする
exec 9>"$PENDING.lock"
flock -n 9 || { echo "LOCK_BUSY"; exit 1; }

# ロック取得成功。pendingの中身を確認
CURRENT=$(cat "$PENDING" 2>/dev/null | tr -d '[:space:]')

if [ -z "$CURRENT" ]; then
  # 誰もclaim してない → 自分の名前を書く
  echo "$CLAIMER" > "$PENDING"
  echo "CLAIMED"
elif [ "$CURRENT" = "$CLAIMER" ]; then
  # 自分が既にclaim済み
  echo "CLAIMED"
else
  # 他の人がclaim済み
  echo "TAKEN_BY $CURRENT"
fi

exec 9>&-
