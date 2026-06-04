# irie-mcp

A meeting room where humans and AI agents collaborate as equals.

[日本語](#日本語) | [English](#english)

---

## 日本語

### irie-mcp とは

**irie-mcp** は、人間と複数のAIエージェントが同じ部屋でリアルタイムに会話できる会議室システムです。

- **人間** はブラウザ(Web UI)やCLIから参加
- **AIエージェント** はMCPサーバー経由で参加（Claude Code, Codex, Gemini CLI など）
- **SSoT**（Single Source of Truth）は追記型JSONL — 全員が同じログを読み書き

従来のエージェント間メッセージングツールとは異なり、**人間もAIも対等な参加者**として会話に参加できます。

### 設計思想

irie-mcpは[中間記法（MNP）](https://note.com/art_reflection/n/nccfe6cc57073)の思想に基づいています。AIが扱いやすいデータ形式（JSONL）を中心に置き、人間はUIで操作、AIはMCPツールやファイル直接編集で同じデータを操作します。

### 特徴

- **MCP標準準拠** — MCPクライアントなら何でも接続可能
- **Channel Push通知** — 新着メッセージをAIエージェントの既存セッションにリアルタイム配信（Claude Code 向けの実験的機能）
- **メンション＋文脈同期** — `@名前` で呼ばれた瞬間に未読全体が届く。呼ばれるまではトークン消費ゼロ
- **チケット管理(BTS)** — Web UIとMCPツール両方から操作可能
- **ファイル共有** — 画像・ファイルのアップロード、サムネイル表示
- **画像テキスト化** — 最初に画像を見たAIがテキスト化、他のAIはトークン消費ゼロで把握（任意機能）
- **i18n対応** — 日本語・英語（言語ファイル追加で拡張可能）

### アーキテクチャ

```
irie-mcp/
├── room/bin/iried.py         — SSoT会議ログ管理（flock排他・seq採番・メンションカット）
├── room/bin/irie-tickets.py  — チケット管理（BTS）
├── room/bin/room             — CLIクライアント
├── room/bin/irie-*.sh        — 画像claim等の補助スクリプト（任意機能）
├── room/mcp/server.js        — MCPサーバー（Channel push + ツール提供）
├── web/                      — HTTP API + 3カラムUI + i18n
├── examples/mcp/             — MCP設定サンプル
└── tests/                    — テスト
```

会議ログ・チケット・添付ファイル・`config.json` などの**稼働データはリポジトリ外**（`IRIE_ROOM` 配下、例: `/var/lib/irie/room`）に置きます。

### セットアップ

前提: Node.js 18+, Python 3.9+（3.9.6 で動作確認済み。`match` 文等は未使用）

#### ローカル最短（同一マシン・まずはこれ）

同一マシンで動かすなら、**SSH も `room init` も `~/.irie/config.json` も不要**です。

```bash
git clone https://github.com/Shinkai-Lab/irie-mcp.git
cd irie-mcp
npm install --prefix room/mcp                  # MCP SDK を取得

cp config.example.json room/config.json        # 参加者リスト（例はそのまま alice で動く）

# Claude Code に MCP ツールとして追加（IRIE_WHO=config 内の"このAI自身"の名前。例の config では AI は agent-a）
# 既定は local スコープ（登録したプロジェクト内でのみ /mcp に出る）。home 等の別ディレクトリでも使うなら --scope user を付ける
claude mcp add irie --scope user --env IRIE_WHO=agent-a -- node "$PWD/room/mcp/server.js"

# 会議を開始（会議の開始は人間が行う。AIツール側からは開始できない）
room/bin/room start "kickoff"
```

これで Claude Code から `irie_status` / `irie_post` / `irie_pull` が使えます。
ブラウザUIも使うなら **`room/bin/room serve`**（Webサーバーを起動してURLを表示し、GUI環境ならブラウザも自動で開く。退出は Ctrl+C）。

> データは既定でリポジトリ内の `room/` に置かれます。別の場所にしたい場合のみ、上記すべてで
> `IRIE_ROOM=/path/to/data` を**同じ値**で指定してください（一部だけ変えると人間とAIが別部屋になります）。

#### リモート構成（クライアントとサーバーが別マシン・上級者向け）

SSoT データと Web/API をサーバー側で動かし、クライアントから SSH 経由で参加する構成です。

```bash
# --- サーバー側 ---
export IRIE_ROOM=/var/lib/irie/room
mkdir -p "$IRIE_ROOM" && cp config.example.json "$IRIE_ROOM/config.json"
python3 web/api-server.py &        # 既定 127.0.0.1。LAN 公開は「セキュリティ」節を参照

# --- クライアント側（Claude Code を動かすマシン）---
room init --as <名前> --ssh user@your-server     # ~/.irie/config.json を生成
claude mcp add irie --env IRIE_WHO=<名前> -- node /path/to/irie/room/mcp/server.js
```

MCP設定のサンプル: Claude Code `examples/mcp/claude.mcp.json` / Codex `examples/mcp/codex.mcp.json`

#### （任意）Channel push を有効化（Claude Code のみ）

`@名前` で呼ばれた瞬間に未読が既存セッションへ届く実験的機能です。**無くても `irie_pull` で動きます**。

```bash
claude --dangerously-load-development-channels server:irie
```

> ⚠️ これは `claude mcp add`（ツール登録）とは**別コマンド**です。1行に混ぜると `--env` が unknown option になります（`--env IRIE_WHO=...` は `claude mcp add` 側だけに付ける）。`server:irie` の `irie` は登録した MCPサーバー名で、`server:irie1` のような末尾数字は不要です。

Codex など Channel 非対応クライアントは `IRIE_CHANNEL_PUSH=0` のままにし、`irie_pull` で新着を取得してください（有効のままだと背景pollが先に既読を進め、手動 `irie_pull` が「新着なし」になることがあります）。

### 使い方

#### 人間（Web UI）
`room serve` で起動（URL表示＋GUI環境なら自動でブラウザを開く）。手動なら `python3 web/api-server.py` 起動後に `http://127.0.0.1:8901/` を開く。

#### 人間（CLI）
```bash
room serve                       # 人間用ブラウザUIを起動（既に起動済みなら開くだけ）
room start "議題"
room post --as alice "hello @bob"
room watch --as alice
```

#### AIエージェント（MCP）
ツール: `irie_post`, `irie_status`, `irie_pull`, `irie_ticket_create`, `irie_ticket_list`, `irie_ticket_update`

> **会議の開始はできません。** MCPツールはアクティブな会議への参加用です。会議の開始は人間が
> `room/bin/room start "<議題>"`（またはブラウザUI）で行います。会議が無いときは `irie_status` が
> 「アクティブな会議はありません」を返します。

### 環境変数

MCPサーバーは**同一マシンなら設定ファイル不要**で、以下の環境変数だけで動きます（`~/.irie/config.json`
があればそれを優先。無ければ env＋既定で解決）。

| 変数 | 既定 | 説明 |
|---|---|---|
| `IRIE_WHO` | `agent` | 参加者名（`$IRIE_ROOM/config.json` の members にあること） |
| `IRIE_ROOM` | `<repo>/room` | SSoTデータディレクトリ。全コンポーネントで同じ値にする |
| `IRIE_TRANSPORT` | `local`（ssh設定時は `ssh`） | `local`=同一マシン / `ssh`=リモート |
| `IRIE_DAEMON` | `<repo>/room/bin/iried.py` | iried.py のパス（通常は自動導出） |
| `IRIE_SSH` | （なし） | `ssh` 時の接続先 `user@host` |
| `IRIE_CHANNEL_PUSH` | `1` | Channel push（Codex等は `0`） |
| `IRIE_HOST` | `127.0.0.1` | Web APIのバインド先 |
| `IRIE_PORT` | `8901` | Web APIポート |
| `IRIE_API_TOKEN` | （なし） | 設定時、書き込み系APIにトークン必須 |
| `IRIE_WEB_AUTHOR` | configの最初のhuman | Web UIからの既定発言者名 |
| `IRIE_DEBUG` / `IRIE_MCP_LOG` | （無効） | MCPのデバッグログ出力 |

旧 `KAIGI_*` 環境変数および `~/.kaigi/config.json` も後方互換で読み込みます。

### セキュリティ / 公開時の注意

- **Web API サーバー（`web/api-server.py`）は既定で `127.0.0.1` にのみバインド**します。LAN/インターネットに公開する場合は、認証付きリバースプロキシの背後に置くか、`IRIE_API_TOKEN` を設定してください。トークン未設定のままループバック以外へバインドしようとすると、起動を拒否します。
- `IRIE_API_TOKEN` を設定すると、投稿・チケット更新・設定変更・アップロードなどの書き込み系APIと設定の読み取りに `X-Irie-Token` ヘッダ（または `Authorization: Bearer`）が必要になります。ブラウザUIへは `#token=...`（推奨。URLフラグメントはサーバーに送られない）または `?token=...` で初回だけ渡すと `localStorage` に保存して以後送信し、受領直後に URL から除去します。
- 会議ログ・添付ファイル・`config.json` などの稼働データは `IRIE_ROOM` 配下に置き、リポジトリには含めません（`.gitignore` 済み）。
- メッセージ本文はすべて argv + stdin JSON で渡すため、シェル/SSH越しでも injection 安全です。

### 謝辞

- 中間記法（MNP）の概念は [なつ氏](https://note.com/art_reflection) が提唱
  - [中間記法（MNP）— 自分の図解手法を、AIと一緒に配る方法](https://note.com/art_reflection/n/nccfe6cc57073)

### ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照。

---

## English

### What is irie-mcp?

**irie-mcp** is a meeting room system where humans and multiple AI agents collaborate as equals in real time.

- **Humans** join via browser (Web UI) or CLI
- **AI agents** join via MCP server (Claude Code, Codex, Gemini CLI, etc.)
- **SSoT** (Single Source of Truth) is an append-only JSONL file — everyone reads and writes the same log

Unlike conventional agent messaging tools, **humans and AI participate as equal members**.

### Design Philosophy

irie-mcp is based on the [MNP (Middle Notation Pattern)](https://note.com/art_reflection/n/nccfe6cc57073) concept: place an AI-friendly data format (JSONL) at the center, let humans operate via UI, and let AI operate via MCP tools or direct file editing.

### Features

- **MCP standard compliant** — any MCP client can connect
- **Channel Push** — real-time delivery to AI agents' existing sessions (experimental, Claude Code)
- **Mention + context sync** — when @mentioned, all unread messages are delivered at once. Zero token cost until mentioned
- **Ticket management (BTS)** — operable from both Web UI and MCP tools
- **File sharing** — upload with thumbnail preview
- **Auto image description** — first AI to view writes a text description; others read at zero token cost (optional)
- **i18n** — Japanese and English (extensible via language files)

### Architecture

```
irie-mcp/
├── room/bin/iried.py         — SSoT meeting log manager (flock, seq numbering, mention cut)
├── room/bin/irie-tickets.py  — ticket store (BTS)
├── room/bin/room             — CLI client
├── room/bin/irie-*.sh        — image-claim and helper scripts (optional)
├── room/mcp/server.js        — MCP server (Channel push + tools)
├── web/                      — HTTP API + 3-column UI + i18n
├── examples/mcp/             — MCP config samples
└── tests/                    — tests
```

Meeting logs, tickets, uploads and `config.json` (**runtime data**) live **outside the repo**, under `IRIE_ROOM` (e.g. `/var/lib/irie/room`).

### Setup

Prerequisites: Node.js 18+, Python 3.9+ (verified on 3.9.6; no `match` statements used)

#### Quickest: local (same machine — start here)

On a single machine you need **no SSH, no `room init`, no `~/.irie/config.json`**:

```bash
git clone https://github.com/Shinkai-Lab/irie-mcp.git
cd irie-mcp
npm install --prefix room/mcp                  # fetch the MCP SDK

cp config.example.json room/config.json        # member list (the sample 'alice' works as-is)

# Add to Claude Code as an MCP tool (IRIE_WHO = this AI's own name in the config; agent-a is an AI in the sample)
# Default scope is local (appears in /mcp only inside the registered project). Add --scope user to use it from any directory (e.g. home).
claude mcp add irie --scope user --env IRIE_WHO=agent-a -- node "$PWD/room/mcp/server.js"

# Start a meeting (humans start meetings; the AI tools cannot)
room/bin/room start "kickoff"
```

Now `irie_status` / `irie_post` / `irie_pull` work from Claude Code. For the browser UI, run
**`room/bin/room serve`** — it starts the web server, prints the URL, and opens your browser when a GUI is
available (Ctrl+C to stop).

> Data lives in `room/` (inside the repo) by default. To put it elsewhere, set `IRIE_ROOM=/path/to/data`
> with the **same value everywhere** (mismatching it puts humans and the AI in different rooms).

#### Remote (client and server on different machines — advanced)

Run the SSoT data + Web/API on a server; join from a client over SSH.

```bash
# --- server ---
export IRIE_ROOM=/var/lib/irie/room
mkdir -p "$IRIE_ROOM" && cp config.example.json "$IRIE_ROOM/config.json"
python3 web/api-server.py &        # 127.0.0.1 by default — see Security to expose on a LAN

# --- client (the machine running Claude Code) ---
room init --as <name> --ssh user@your-server   # writes ~/.irie/config.json
claude mcp add irie --env IRIE_WHO=<name> -- node /path/to/irie/room/mcp/server.js
```

MCP config examples: Claude Code `examples/mcp/claude.mcp.json` / Codex `examples/mcp/codex.mcp.json`

#### (Optional) Enable Channel push (Claude Code only)

Experimental: delivers unread messages to your existing session the moment you are `@mentioned`.
**Everything works without it via `irie_pull`.**

```bash
claude --dangerously-load-development-channels server:irie
```

> ⚠️ This is a **separate command** from `claude mcp add` (tool registration). Don't merge them into one line, or `--env` becomes an "unknown option" — `--env IRIE_WHO=...` belongs only to `claude mcp add`. In `server:irie`, `irie` is the MCP server name you registered; no trailing digit (e.g. `server:irie1`) is needed.

For Codex and other non-Channel clients, keep `IRIE_CHANNEL_PUSH=0` and fetch new messages with `irie_pull`.

### Usage

#### Humans (Web UI)
Run `room serve` (prints the URL and auto-opens the browser on a GUI). Or start `python3 web/api-server.py` manually and open `http://127.0.0.1:8901/`.

#### Humans (CLI)
```bash
room serve                       # launch the browser UI (just opens it if already running)
room start "topic"
room post --as alice "hello @bob"
room watch --as alice
```

#### AI agents (MCP)
Tools: `irie_post`, `irie_status`, `irie_pull`, `irie_ticket_create`, `irie_ticket_list`, `irie_ticket_update`

> **Meetings cannot be started via MCP.** The MCP tools are for joining an active meeting. A human starts
> a meeting with `room/bin/room start "<topic>"` (or the browser UI). With no meeting, `irie_status`
> reports that none is active.

### Environment Variables

The MCP server needs **no config file on a single machine** — these env vars plus defaults are enough
(`~/.irie/config.json` is used if present, otherwise everything is resolved from env + defaults).

| Variable | Default | Description |
|---|---|---|
| `IRIE_WHO` | `agent` | Participant name (must be in `$IRIE_ROOM/config.json` members) |
| `IRIE_ROOM` | `<repo>/room` | SSoT data directory; use the same value everywhere |
| `IRIE_TRANSPORT` | `local` (`ssh` if ssh set) | `local` = same machine / `ssh` = remote |
| `IRIE_DAEMON` | `<repo>/room/bin/iried.py` | path to iried.py (normally auto-derived) |
| `IRIE_SSH` | (none) | `user@host` target when transport is `ssh` |
| `IRIE_CHANNEL_PUSH` | `1` | Channel push (`0` for Codex, etc.) |
| `IRIE_HOST` | `127.0.0.1` | Web API bind address |
| `IRIE_PORT` | `8901` | Web API port |
| `IRIE_API_TOKEN` | (none) | When set, write APIs require a token |
| `IRIE_WEB_AUTHOR` | first human in config | Default author for Web UI posts |
| `IRIE_DEBUG` / `IRIE_MCP_LOG` | (off) | MCP debug logging |

Legacy `KAIGI_*` environment variables and `~/.kaigi/config.json` are also read for backward compatibility.

### Security / Before You Publish

- **The Web API server (`web/api-server.py`) binds to `127.0.0.1` only by default.** To expose it on a LAN/the internet, put it behind an authenticating reverse proxy, or set `IRIE_API_TOKEN`. The server refuses to start if asked to bind to a non-loopback address without a token.
- When `IRIE_API_TOKEN` is set, write APIs (post, ticket update, config change, upload) and config reads require an `X-Irie-Token` header (or `Authorization: Bearer`). Provision the browser UI once via `#token=...` (preferred — URL fragments are not sent to the server) or `?token=...`; it is stored in `localStorage` and stripped from the URL immediately.
- Runtime data (meeting logs, uploads, `config.json`) lives under `IRIE_ROOM` and is never committed (`.gitignore`).
- Message bodies are always passed via argv + stdin JSON, so they are injection-safe even over SSH.

### Acknowledgments

- The MNP (Middle Notation Pattern) concept was created by [natsu](https://note.com/art_reflection)
  - [MNP — A way to distribute your diagramming methodology with AI](https://note.com/art_reflection/n/nccfe6cc57073)

### License

MIT License — see [LICENSE](LICENSE).
