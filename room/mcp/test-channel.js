#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const mcp = new Server(
  { name: "test-channel", version: "0.0.1" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
    },
    instructions: "テスト用チャンネル。<channel source=\"test-channel\"> で届いたメッセージに「届いた！」と言ってください。",
  }
);

await mcp.connect(new StdioServerTransport());
console.error("[test-channel] connected, sending test notification in 3s...");

setTimeout(async () => {
  try {
    await mcp.notification({
      method: "notifications/claude/channel",
      params: {
        content: "これはテスト通知です。見えていたら「テスト成功！」と言ってください。",
        meta: {},
      },
    });
    console.error("[test-channel] notification sent!");
  } catch (e) {
    console.error("[test-channel] notification FAILED:", e.message);
  }
}, 3000);

// Keep alive
setInterval(() => {}, 60000);
