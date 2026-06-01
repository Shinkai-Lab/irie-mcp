#!/usr/bin/env python3
"""irie-describe-image.py — アップ画像をOllamaで自動テキスト化（任意機能）

使い方:
  python3 irie-describe-image.py <image_path>
  → 画像の説明テキストをstdoutに出力

Web API サーバーからアップロード後にバックグラウンドで呼ぶ想定。
結果はmeta.jsonlのdescriptionフィールドに格納する。

環境変数:
  IRIE_VISION_MODEL — Ollama のビジョンモデル名 (既定: gemma4:e2b。環境のモデルに合わせて指定する)
  IRIE_OLLAMA_URL   — Ollama の generate API URL (既定: http://127.0.0.1:11434/api/generate)
"""
import sys
import json
import subprocess
import base64
import os

OLLAMA_MODEL = os.environ.get("IRIE_VISION_MODEL") or os.environ.get("KAIGI_VISION_MODEL") or "gemma4:e2b"
OLLAMA_URL = os.environ.get("IRIE_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")

def describe(image_path):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": "この画像の内容を日本語で簡潔に説明してください。何が写っているか、テキストがあれば読み取ってください。3文以内で。",
        "images": [b64],
        "stream": False,
    }

    r = subprocess.run(
        ["curl", "-s", OLLAMA_URL,
         "-d", json.dumps(payload)],
        capture_output=True, text=True, timeout=120,
    )

    if r.returncode != 0:
        return f"(画像認識失敗: {r.stderr[:200]})"

    try:
        resp = json.loads(r.stdout)
        return resp.get("response", "(応答なし)").strip()
    except json.JSONDecodeError:
        return f"(パース失敗: {r.stdout[:200]})"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: irie-describe-image.py <image_path>")
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit(f"ファイルが見つかりません: {path}")
    print(describe(path))
