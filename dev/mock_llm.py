#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地模拟大模型（仅用于测试 server.py，不参与正式使用）

用法:
    python dev/mock_llm.py --port 8799 --mode good
    mode: good          正常返回（对话流式 + 评估 JSON）
          bad_analysis  对话正常，但评估总是返回无法解析的纯文本
          empty         对话流式返回空内容（测试自动降级）
          refuse        所有请求返回 401（测试鉴权错误提示）
"""
import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = "good"

REPLY = (
    "我注意到一个很关键的细节：你理想中的早晨里有**晨读**、有秩序感，"
    "但你把它放在了「上班之前」。\n\n"
    "这让我看到一个矛盾——你追求的是自主、有序的生活节奏，"
    "但你的日常主线仍然是「去上班」。\n\n"
    "**我的第二个问题：**\n\n"
    "如果要求你未来三年的收入必须来自「你自己创造的价值」，"
    "而不是上班的薪水，你第一时间浮上来的那个理由，是「我做不到」还是「我可以」？"
)

ANALYSIS = {
    "scores": {"mind": 1.9, "body": 2.0, "spirit": 1.6, "vocation": 1.4},
    "roadmap": ["把下班后1小时划给副业", "停止为内部考试过度投入"],
    "timeline_event": "识破「打工者心智」",
}

ANALYSIS_2 = {
    "scores": {"mind": 2.2, "body": 2.0, "spirit": 1.8, "vocation": 1.7},
    "roadmap": ["跑通一次 LibTV 完整工作流", "本周产出一集短剧分镜"],
    "timeline_event": "确立破局第一步",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

        if MODE == "refuse":
            return self._send(401, b'{"error":{"message":"Authentication Fails, Your api key is invalid"}}')

        system = ""
        for m in payload.get("messages", []):
            if m.get("role") == "system":
                system += m.get("content", "")
        is_analysis = "评估引擎" in system
        stream = bool(payload.get("stream"))

        if is_analysis:
            if MODE == "bad_analysis":
                body = {"choices": [{"message": {"content": "我觉得这位用户的评分大概在中等水平吧，具体要看情况。"}}]}
                return self._send(200, json.dumps(body).encode())
            turn = len([m for m in payload.get("messages", []) if m.get("role") == "user"])
            blob = json.dumps(payload.get("messages", []), ensure_ascii=False)
            data = ANALYSIS_2 if ("副业" in blob or turn > 1) else ANALYSIS
            body = {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}
            return self._send(200, json.dumps(body).encode())

        if not stream:
            body = {"choices": [{"message": {"content": REPLY}}]}
            return self._send(200, json.dumps(body).encode())

        # ---- 流式 ----
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        if MODE == "empty":
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        try:
            for piece in re.findall(r".{1,12}", REPLY, re.S):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.02)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):  # noqa: N802
        self._send(200, b'{"ok":true}')

    def _send(self, code, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--mode", default="good", choices=["good", "bad_analysis", "empty", "refuse"])
    a = ap.parse_args()
    MODE = a.mode
    print(f"mock llm on http://127.0.0.1:{a.port}/v1  mode={MODE}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
