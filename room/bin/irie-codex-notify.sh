#!/bin/bash
# irie-codex-notify.sh — @Codex検知でtmux経由でCodexにメッセージ注入（任意機能）
#
# 使い方:
#   IRIE_ROOM=/opt/irie/room TMUX_TARGET=codex ./irie-codex-notify.sh
#
# 環境変数:
#   IRIE_ROOM     — 会議室のSSoTディレクトリ (必須。旧 KAIGI_ROOM も可)
#   TMUX_TARGET   — Codexが動いてるtmuxセッション名 (デフォルト: codex)
#   CODEX_NAME    — 監視するメンション名 (デフォルト: Codex)
#   POLL_INTERVAL — ポーリング間隔秒 (デフォルト: 3)

ROOM="${IRIE_ROOM:-${KAIGI_ROOM:?IRIE_ROOM is required}}"
TMUX_TARGET="${TMUX_TARGET:-codex}"
CODEX_NAME="${CODEX_NAME:-Codex}"
POLL_INTERVAL="${POLL_INTERVAL:-3}"

ACTIVE_FILE="$ROOM/ACTIVE"
CURSOR_FILE=""
LAST_SEQ=0

get_active() {
  cat "$ACTIVE_FILE" 2>/dev/null | tr -d '[:space:]'
}

update_cursor_path() {
  local mid="$1"
  CURSOR_FILE="$ROOM/cursor/${mid}__${CODEX_NAME}_notify"
}

get_cursor() {
  cat "$CURSOR_FILE" 2>/dev/null | tr -d '[:space:]'
  echo "${LAST_SEQ:-0}"
}

save_cursor() {
  echo "$1" > "$CURSOR_FILE"
}

echo "[irie-codex-notify] Started. Watching for @${CODEX_NAME} in ${ROOM}"
echo "[irie-codex-notify] tmux target: ${TMUX_TARGET}"

while true; do
  MID=$(get_active)
  if [ -z "$MID" ]; then
    sleep "$POLL_INTERVAL"
    continue
  fi

  update_cursor_path "$MID"
  LAST_SEQ=$(cat "$CURSOR_FILE" 2>/dev/null | tr -d '[:space:]')
  LAST_SEQ="${LAST_SEQ:-0}"

  LOG_FILE="$ROOM/${MID}.jsonl"
  if [ ! -f "$LOG_FILE" ]; then
    sleep "$POLL_INTERVAL"
    continue
  fi

  # 新着の中に@Codexか@allがあるか確認
  HIT=$(awk -v last="$LAST_SEQ" -v name="$CODEX_NAME" '
    {
      match($0, /"seq": *([0-9]+)/, arr)
      seq = arr[1]
      if (seq > last && ($0 ~ "@"name || $0 ~ "@all")) {
        print seq
      }
    }
  ' "$LOG_FILE" | tail -1)

  # 最新seqを取得してcursor更新
  NEWEST=$(awk '{ match($0, /"seq": *([0-9]+)/, arr); print arr[1] }' "$LOG_FILE" | tail -1)

  if [ -n "$HIT" ]; then
    echo "[irie-codex-notify] @${CODEX_NAME} detected at seq ${HIT}, sending to tmux:${TMUX_TARGET}"
    tmux send-keys -t "$TMUX_TARGET" "irie_pull で新着メッセージを読んで、必要があれば irie_post で返事して。" 2>/dev/null
    sleep 0.5
    tmux send-keys -t "$TMUX_TARGET" Enter 2>/dev/null
    if [ $? -ne 0 ]; then
      echo "[irie-codex-notify] WARNING: tmux send-keys failed. Session '${TMUX_TARGET}' not found?"
    fi
    save_cursor "$NEWEST"
  fi
  # @なしの新着はcursorを進めない（文脈同期を壊さないため）

  sleep "$POLL_INTERVAL"
done
