#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { spawnSync } from "node:child_process";
import { readFileSync, appendFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const WHO = process.env.IRIE_WHO || process.env.KAIGI_WHO || "agent";
const POLL_INTERVAL_MS = Number(process.env.IRIE_POLL_INTERVAL_MS || process.env.KAIGI_POLL_INTERVAL_MS || 3000);
const CHANNEL_PUSH_ENABLED = !["0", "false", "off", "no"].includes(
  String(process.env.IRIE_CHANNEL_PUSH ?? process.env.KAIGI_CHANNEL_PUSH ?? "1").toLowerCase()
);
// 設定は ~/.irie/config.json。旧 ~/.kaigi も後方互換で読む。
const CONFIG_DIR = existsSync(join(homedir(), ".irie", "config.json"))
  ? join(homedir(), ".irie")
  : existsSync(join(homedir(), ".kaigi", "config.json"))
    ? join(homedir(), ".kaigi")
    : join(homedir(), ".irie");
const CONFIG_PATH = join(CONFIG_DIR, "config.json");
// MCP stdout payload には会話内容が含まれるため、ログは既定で無効。
// IRIE_DEBUG=1 もしくは IRIE_MCP_LOG=<path> を設定したときだけ記録する。
const LOG_ENABLED = !!(process.env.IRIE_MCP_LOG
  || ["1", "true", "on", "yes"].includes(String(process.env.IRIE_DEBUG || "").toLowerCase()));
const LOG_FILE = process.env.IRIE_MCP_LOG || join(CONFIG_DIR, "mcp.log");
// MCPサーバー自身の位置から iried.py を導出（設定ファイル無しでローカル動作可能にする）
// このファイルは room/mcp/server.js → デーモンは room/bin/iried.py
const DEFAULT_DAEMON = join(dirname(fileURLToPath(import.meta.url)), "..", "bin", "iried.py");

// POSIX シェル用の最小クォート（リモートSSHコマンド文字列の injection 防止）
function shq(s) { return `'${String(s).replace(/'/g, "'\\''")}'`; }
const INSTRUCTIONS = CHANNEL_PUSH_ENABLED
  ? `You are connected to irie-mcp, a meeting room where humans and AI agents collaborate in real time.

When a <channel source="irie"> message arrives, read it and reply with irie_post if you are mentioned (@your_name or @all). If not mentioned, stay silent.

Available tools:
- irie_post: Post a message to the meeting room
- irie_status: Get current meeting status
- irie_pull: Manually fetch unread messages
- irie_ticket_create: Create a ticket
- irie_ticket_list: List tickets
- irie_ticket_update: Update a ticket (status/assignee/comment)

Image rules:
- When you see [添付: ...] in a message, DO NOT read the image URL directly
- Use irie_image_read(id) to check if a description exists
- If no description: use irie_image_claim(id) to claim it, then irie_image_describe(id, description) to save your description
- If description exists: read the text (zero token cost)`
  : `You are connected to irie-mcp, a meeting room where humans and AI agents collaborate in real time.

Channel push is disabled for this client. Use irie_pull when the user asks you to check the room, then reply with irie_post if needed.

Available tools:
- irie_post: Post a message to the meeting room
- irie_status: Get current meeting status
- irie_pull: Fetch unread messages and advance your cursor
- irie_ticket_create: Create a ticket
- irie_ticket_list: List tickets
- irie_ticket_update: Update a ticket (status/assignee/comment)

Image rules:
- When you see [添付: ...] in a message, DO NOT read the image URL directly
- Use irie_image_read(id) to check if a description exists
- If no description: use irie_image_claim(id) to claim it, then irie_image_describe(id, description) to save your description
- If description exists: read the text (zero token cost)`;

// stdout傍受デバッグ: MCPのstdout payloadは会話内容を含むため、既定では記録しない。
// IRIE_DEBUG / IRIE_MCP_LOG を設定したときだけ stdout を傍受してログに残す。
if (LOG_ENABLED) {
  const _origStdoutWrite = process.stdout.write.bind(process.stdout);
  process.stdout.write = function(data, ...args) {
    try {
      const str = typeof data === "string" ? data : data.toString("utf8");
      appendFileSync(LOG_FILE, `[STDOUT] ${str.trim().slice(0, 500)}\n`);
    } catch {}
    return _origStdoutWrite(data, ...args);
  };
}

function log(msg) {
  console.error(`[irie-mcp] ${msg}`);
  if (!LOG_ENABLED) return;
  const line = `[${new Date().toISOString()}] ${msg}`;
  try { appendFileSync(LOG_FILE, line + "\n"); } catch {}
}

// 設定ファイルは任意。あれば読む、無ければ {} を返す（ローカルは env + 既定で動く）。
function loadConfig() {
  try {
    return JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
  } catch (e) {
    if (e.code === "ENOENT") return {};
    throw new Error(`設定ファイルの読み込みに失敗しました (${CONFIG_PATH}): ${e.message}`);
  }
}

// 設定ファイル / 環境変数 / 既定値から実効設定を組み立てる。
// 同一マシンなら設定ファイルもSSHも不要（transport=local が既定）。
// 設定キー daemon は旧 "kaigid" も後方互換で読む。
function resolveConfig() {
  const cfg = loadConfig();
  const ssh = cfg.ssh || process.env.IRIE_SSH || "";
  const transport = cfg.transport || process.env.IRIE_TRANSPORT || (ssh ? "ssh" : "local");
  const daemon = cfg.daemon || cfg.kaigid || process.env.IRIE_DAEMON || DEFAULT_DAEMON;
  const python = cfg.python || process.env.IRIE_PYTHON || "python3";
  const room = cfg.room || process.env.IRIE_ROOM || process.env.KAIGI_ROOM || "";
  if (transport !== "local" && !ssh) {
    throw new Error(
      `transport=${transport} には SSH 接続先が必要です。~/.irie/config.json に "ssh":"user@host" を` +
      ` 設定するか（または IRIE_SSH 環境変数）、同一マシンなら transport を "local"（既定）にしてください。`
    );
  }
  if (transport === "local" && !existsSync(daemon)) {
    throw new Error(
      `iried.py が見つかりません: ${daemon}。環境変数 IRIE_DAEMON で明示するか、` +
      `リポジトリ構成（room/bin/iried.py）を確認してください。`
    );
  }
  return { transport, daemon, python, room, ssh };
}

function callDaemon(cmd, payload) {
  const { transport, daemon, python: py, room, ssh } = resolveConfig();
  const data = JSON.stringify(payload);
  const childEnv = room ? { ...process.env, IRIE_ROOM: room, KAIGI_ROOM: room } : process.env;
  let argv;
  if (transport === "local") {
    argv = [py, daemon, cmd];
  } else {
    const remote = [py, daemon, cmd].map(shq).join(" ");
    const envPrefix = room ? `IRIE_ROOM=${shq(room)} KAIGI_ROOM=${shq(room)} ` : "";
    argv = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
            ssh, `${envPrefix}${remote}`];
  }

  const result = spawnSync(argv[0], argv.slice(1), {
    input: data,
    encoding: "utf8",
    timeout: 15000,
    env: transport === "local" ? childEnv : process.env,
  });

  if (result.status !== 0) {
    throw new Error(`呼び出し失敗: ${result.stderr?.trim() || "unknown"}`);
  }

  return JSON.parse(result.stdout);
}

function callTickets(cmd, payload) {
  const { transport, daemon, python: py, room, ssh } = resolveConfig();
  const data = JSON.stringify(payload);
  // チケットスクリプトはデーモンと同じディレクトリの irie-tickets.py
  const ticketsScript = join(dirname(daemon), "irie-tickets.py");
  const childEnv = room ? { ...process.env, IRIE_ROOM: room, KAIGI_ROOM: room } : process.env;

  let argv;
  if (transport === "local") {
    argv = [py, ticketsScript, cmd];
  } else {
    const remote = [py, ticketsScript, cmd].map(shq).join(" ");
    const envPrefix = room ? `IRIE_ROOM=${shq(room)} KAIGI_ROOM=${shq(room)} ` : "";
    argv = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
            ssh, `${envPrefix}${remote}`];
  }

  const result = spawnSync(argv[0], argv.slice(1), {
    input: data,
    encoding: "utf8",
    timeout: 15000,
    env: transport === "local" ? childEnv : process.env,
  });

  if (result.status !== 0) {
    throw new Error(`呼び出し失敗: ${result.stderr?.trim() || "unknown"}`);
  }

  return JSON.parse(result.stdout);
}

const mcp = new Server(
  { name: "irie", version: "0.1.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
      tools: {},
    },
    instructions: INSTRUCTIONS,
  }
);

// 画像claim関連ヘルパー
function callClaim(fileId) {
  const { transport, python: py, room } = resolveConfig();
  const claimScript = join(dirname(fileURLToPath(import.meta.url)), "..", "bin", "irie-claim.sh");
  const childEnv = room ? { ...process.env, IRIE_ROOM: room } : process.env;
  if (transport !== "local") throw new Error("画像claimはローカル専用");
  const result = spawnSync("bash", [claimScript, fileId, WHO], {
    encoding: "utf8", timeout: 10000, env: childEnv,
  });
  return { exitCode: result.status, output: (result.stdout || "").trim() };
}

function callFinishClaim(fileId, description) {
  const { transport, room } = resolveConfig();
  const finishScript = join(dirname(fileURLToPath(import.meta.url)), "..", "bin", "irie-finish-claim.sh");
  const childEnv = room ? { ...process.env, IRIE_ROOM: room } : process.env;
  if (transport !== "local") throw new Error("画像claimはローカル専用");
  const result = spawnSync("bash", [finishScript, fileId, WHO, description], {
    encoding: "utf8", timeout: 30000, env: childEnv,
  });
  return { exitCode: result.status, output: (result.stdout || "").trim() };
}

function loadUploadMeta() {
  const { room } = resolveConfig();
  const metaPath = join(room || "room", "uploads", "meta.jsonl");
  try {
    return readFileSync(metaPath, "utf8").split("\n").filter(Boolean).map(l => JSON.parse(l));
  } catch { return []; }
}

const TOOLS = [
  {
    name: "irie_post",
    description: "会議室にメッセージを投稿する",
    inputSchema: {
      type: "object",
      properties: {
        text: {
          type: "string",
          description: "投稿するメッセージ本文",
        },
      },
      required: ["text"],
    },
  },
  {
    name: "irie_status",
    description: "アクティブな会議の状態を取得する",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "irie_pull",
    description: CHANNEL_PUSH_ENABLED
      ? "未読メッセージを手動で取得する（Channelで自動pushされるので通常は不要）"
      : "未読メッセージを手動で取得する（CodexなどChannel非対応クライアント用）",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "irie_ticket_create",
    description: "チケットを作成する",
    inputSchema: {
      type: "object",
      properties: {
        title: { type: "string", description: "チケットのタイトル" },
        description: { type: "string", description: "チケットの詳細（任意）" },
        assignee: { type: "string", description: "担当者（config.json の members 名、任意）" },
      },
      required: ["title"],
    },
  },
  {
    name: "irie_ticket_list",
    description: "チケット一覧を取得する（フィルタ任意）",
    inputSchema: {
      type: "object",
      properties: {
        status: { type: "string", description: "ステータスでフィルタ（open/in_progress/done/closed）" },
        assignee: { type: "string", description: "担当者でフィルタ" },
      },
    },
  },
  {
    name: "irie_ticket_update",
    description: "チケットを更新する（ステータス変更/アサイン/コメント追加）",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "number", description: "チケットID" },
        status: { type: "string", description: "新しいステータス（open/in_progress/done/closed）" },
        assignee: { type: "string", description: "新しい担当者" },
        comment: { type: "string", description: "追加するコメント" },
      },
      required: ["id"],
    },
  },
  {
    name: "irie_image_list",
    description: "共有された画像の一覧を取得する。descriptionがある画像はテキストで内容を確認できる",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "irie_image_read",
    description: "画像の内容を取得する。descriptionがあればテキストを返す(トークン節約)。なければclaimが必要",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "画像のファイルID" },
      },
      required: ["id"],
    },
  },
  {
    name: "irie_image_claim",
    description: "画像のdescription書き込み権を取得する。成功したら画像を読んでdescriptionを書いてください",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "画像のファイルID" },
      },
      required: ["id"],
    },
  },
  {
    name: "irie_image_describe",
    description: "claim済み画像のdescriptionを保存する。他のAIはこのテキストを読むだけで画像を確認できる",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "画像のファイルID" },
        description: { type: "string", description: "画像の説明テキスト" },
      },
      required: ["id", "description"],
    },
  },
];

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOLS,
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;

  switch (name) {
    case "irie_post": {
      const { text } = args;
      try {
        const r = callDaemon("append", { author: WHO, text });
        if (!r.ok) {
          return { content: [{ type: "text", text: `エラー: ${r.error}` }], isError: true };
        }
        let msg = `[${r.seq}] ${WHO} として投稿しました`;
        if (r.mention_cut) msg += "（メンションカット適用）";
        return { content: [{ type: "text", text: msg }] };
      } catch (e) {
        return { content: [{ type: "text", text: `投稿失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_status": {
      try {
        const r = callDaemon("status", {});
        if (!r.ok) {
          return { content: [{ type: "text", text: `エラー: ${r.error}` }], isError: true };
        }
        if (!r.active) {
          return { content: [{ type: "text", text: "アクティブな会議はありません" }] };
        }
        return {
          content: [{
            type: "text",
            text: `会議中: ${r.active}\n議題: ${r.topic || "(無題)"}\n発言数: ${r.count}\n最終seq: ${r.last_seq}`,
          }],
        };
      } catch (e) {
        return { content: [{ type: "text", text: `取得失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_pull": {
      try {
        const r = callDaemon("read", { who: WHO });
        if (!r.ok) {
          return { content: [{ type: "text", text: `エラー: ${r.error}` }], isError: true };
        }
        const fresh = r.fresh || [];
        if (fresh.length === 0) {
          return { content: [{ type: "text", text: "新着メッセージはありません" }] };
        }
        const lines = fresh.map(
          (m) => `[${m.seq}] ${m.author}: ${m.text}`
        );
        if (r.messages?.length) {
          callDaemon("ack", { who: WHO, seq: r.messages[r.messages.length - 1].seq, meeting: r.meeting });
        }
        return { content: [{ type: "text", text: lines.join("\n") }] };
      } catch (e) {
        return { content: [{ type: "text", text: `取得失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_ticket_create": {
      try {
        const r = callTickets("create", {
          title: args.title,
          description: args.description || "",
          assignee: args.assignee || null,
          created_by: WHO,
        });
        if (!r.ok) {
          return { content: [{ type: "text", text: `エラー: ${r.error}` }], isError: true };
        }
        return { content: [{ type: "text", text: `チケット #${r.ticket.id} 作成: ${r.ticket.title}` }] };
      } catch (e) {
        return { content: [{ type: "text", text: `作成失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_ticket_list": {
      try {
        const r = callTickets("list", { status: args.status, assignee: args.assignee });
        if (!r.ok) {
          return { content: [{ type: "text", text: `エラー: ${r.error}` }], isError: true };
        }
        if (r.tickets.length === 0) {
          return { content: [{ type: "text", text: "チケットはありません" }] };
        }
        const lines = r.tickets.map(
          (t) => `#${t.id} [${t.status}] ${t.title} (${t.assignee || "未アサイン"})`
        );
        return { content: [{ type: "text", text: lines.join("\n") }] };
      } catch (e) {
        return { content: [{ type: "text", text: `取得失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_ticket_update": {
      try {
        const payload = { id: args.id };
        if (args.status) payload.status = args.status;
        if (args.assignee !== undefined) payload.assignee = args.assignee;
        if (args.comment) { payload.comment = args.comment; payload.comment_by = WHO; }
        const r = callTickets("update", payload);
        if (!r.ok) {
          return { content: [{ type: "text", text: `エラー: ${r.error}` }], isError: true };
        }
        return { content: [{ type: "text", text: `#${r.ticket.id} 更新: ${r.changed.join(", ")}` }] };
      } catch (e) {
        return { content: [{ type: "text", text: `更新失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_image_list": {
      try {
        const meta = loadUploadMeta();
        const images = meta.filter(e => e.is_image);
        if (images.length === 0) {
          return { content: [{ type: "text", text: "共有画像はありません" }] };
        }
        const lines = images.map(e => {
          const status = e.description ? `✅ described by ${e.claimed_by}` : "⏳ description未作成";
          return `[${e.id}] ${e.name} — ${status}`;
        });
        return { content: [{ type: "text", text: lines.join("\n") }] };
      } catch (e) {
        return { content: [{ type: "text", text: `取得失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_image_read": {
      try {
        const meta = loadUploadMeta();
        const img = meta.find(e => e.id === args.id && e.is_image);
        if (!img) {
          return { content: [{ type: "text", text: `画像 ${args.id} が見つかりません` }], isError: true };
        }
        if (img.description) {
          return { content: [{ type: "text", text: `[${img.id}] ${img.name}\n説明: ${img.description}\n(described by ${img.claimed_by})` }] };
        }
        return { content: [{ type: "text", text: `[${img.id}] ${img.name}\ndescriptionがありません。irie_image_claim で取得権を取ってから画像を読んで irie_image_describe で説明を書いてください。` }] };
      } catch (e) {
        return { content: [{ type: "text", text: `取得失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_image_claim": {
      try {
        const meta = loadUploadMeta();
        const img = meta.find(e => e.id === args.id && e.is_image);
        if (!img) {
          return { content: [{ type: "text", text: `画像 ${args.id} が見つかりません` }], isError: true };
        }
        if (img.description) {
          return { content: [{ type: "text", text: `既にdescription済みです:\n${img.description}\n(described by ${img.claimed_by})` }] };
        }
        const result = callClaim(args.id);
        if (result.output === "CLAIMED") {
          const { room } = resolveConfig();
          const imgPath = join(room || "room", "uploads", img.stored);
          return { content: [
            { type: "text", text: `claim成功。画像を読んで irie_image_describe で説明を書いてください。` },
            { type: "image", data: readFileSync(imgPath).toString("base64"), mimeType: img.mime },
          ]};
        } else if (result.output.startsWith("TAKEN_BY")) {
          return { content: [{ type: "text", text: `${result.output} — 他のAIがclaim中です。完了を待ってください。` }] };
        } else {
          return { content: [{ type: "text", text: `claim失敗: ${result.output}` }], isError: true };
        }
      } catch (e) {
        return { content: [{ type: "text", text: `claim失敗: ${e.message}` }], isError: true };
      }
    }

    case "irie_image_describe": {
      try {
        const result = callFinishClaim(args.id, args.description);
        if (result.exitCode === 0) {
          return { content: [{ type: "text", text: `画像 ${args.id} のdescriptionを保存しました。他のAIはテキストで確認できます。` }] };
        }
        return { content: [{ type: "text", text: `保存失敗: ${result.output}` }], isError: true };
      } catch (e) {
        return { content: [{ type: "text", text: `保存失敗: ${e.message}` }], isError: true };
      }
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

function isForMe(msg) {
  const text = msg.text || "";
  const mentionMe = new RegExp(`@${WHO}(?![\\w぀-ヿ一-鿿])`);
  const mentionAll = /@all(?![\w])/;
  return mentionMe.test(text) || mentionAll.test(text);
}

async function pollAndPush() {
  try {
    const r = callDaemon("read", { who: WHO });
    if (!r.ok) {
      log(`Poll: not ok - ${r.error}`);
      return;
    }

    const fresh = r.fresh || [];
    if (fresh.length === 0) return;

    const forMe = fresh.filter(isForMe);
    log(`Poll: ${fresh.length} fresh, ${forMe.length} for me (seq=${fresh.map(m=>m.seq).join(",")})`);

    if (forMe.length > 0) {
      // @自分が含まれてる → 未読全体をpush（文脈同期）
      const lines = fresh.map((m) => `[${m.seq}] ${m.author}: ${m.text}`);
      const content = lines.join("\n");

      const notifPayload = {
        method: "notifications/claude/channel",
        params: {
          content,
          meta: {
            meeting: String(r.meeting),
            who: String(WHO),
            message_count: String(fresh.length),
            last_seq: String(fresh[fresh.length - 1].seq),
          },
        },
      };

      try {
        await mcp.notification(notifPayload);
        log(`Poll: notification sent OK (${fresh.length} msgs, triggered by ${forMe.length} mentions)`);
      } catch (notifErr) {
        log(`Poll: notification FAILED: ${notifErr.message}`);
      }

      if (r.messages?.length) {
        callDaemon("ack", { who: WHO, seq: r.messages[r.messages.length - 1].seq, meeting: r.meeting });
        log(`Poll: acked up to seq=${r.messages[r.messages.length - 1].seq}`);
      }
    }
    // @自分がなければpushもackもしない → 未読として溜まり続ける
  } catch (e) {
    log(`Poll error: ${e.message}`);
  }
}

async function main() {
  log(`Starting irie MCP server (who=${WHO}, channelPush=${CHANNEL_PUSH_ENABLED ? "on" : "off"})`);
  // 実効設定を起動時に1度ログ（診断用）。問題があれば早期に気づける。
  try {
    const c = resolveConfig();
    log(`Config: transport=${c.transport}, daemon=${c.daemon}, room=${c.room || "(iried default: room/)"}`);
  } catch (e) {
    log(`Config warning: ${e.message}`);
  }

  await mcp.connect(new StdioServerTransport());
  log("MCP connected");

  const pollTimer = CHANNEL_PUSH_ENABLED ? setInterval(pollAndPush, POLL_INTERVAL_MS) : null;
  if (!CHANNEL_PUSH_ENABLED) {
    log("Channel push disabled; manual irie_pull will be the only reader that acks messages");
  }

  const cleanup = () => {
    if (pollTimer) clearInterval(pollTimer);
    log("Shutting down");
    process.exit(0);
  };

  process.on("SIGINT", cleanup);
  process.on("SIGTERM", cleanup);
}

main().catch((e) => {
  log(`Fatal: ${e.message}`);
  process.exit(1);
});
