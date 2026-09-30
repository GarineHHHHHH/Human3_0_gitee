#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
降级路径测试：验证「评估失败绝不打扰用户」「流为空自动兜底」「鉴权失败给出清晰提示」

用法（需要先启动 server.py，mock_llm.py 用对应 mode）:
    python dev/degraded_test.py --expect bad_analysis
    python dev/degraded_test.py --expect empty
    python dev/degraded_test.py --expect refuse
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (f"  → {detail}" if detail else ""))


def call(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def chat(session_id, message):
    body = json.dumps({"session_id": session_id, "message": message, "analyze": True}).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/chat", data=body,
                                headers={"Content-Type": "application/json"}, method="POST")
    deltas, events = [], {}
    with urllib.request.urlopen(req, timeout=180) as resp:
        buf = ""
        for raw in resp:
            buf += raw.decode("utf-8", "replace")
            while "\n\n" in buf:
                chunk, buf = buf.split("\n\n", 1)
                name, data = "message", ""
                for line in chunk.split("\n"):
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if name == "delta":
                    deltas.append(payload.get("text", ""))
                else:
                    events[name] = payload
    return deltas, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", required=True, choices=["bad_analysis", "empty", "refuse"])
    a = ap.parse_args()

    _, session = call("POST", "/api/sessions", {})
    sid = session["id"]
    deltas, events = chat(sid, "我最近总在下班后打球到力竭，然后刷抖音到很晚。")
    text = "".join(deltas)
    print(f"场景 = {a.expect}｜收到 {len(deltas)} 个增量，共 {len(text)} 字\n")

    if a.expect == "bad_analysis":
        check("对话仍然完整送达（不受评估失败影响）", len(text) > 30, f"{len(text)} 字")
        check("未向前端推送任何 error 事件（不打扰用户）", "error" not in events)
        check("state.analyzed = false（静默降级）", events.get("state", {}).get("analyzed") is False)
        check("done 仍为成功", events.get("done", {}).get("ok") is True)
        _, stored = call("GET", f"/api/sessions/{sid}")
        check("回复已正常落盘", "晨读" in stored["messages"][1]["content"] or len(stored["messages"][1]["content"]) > 30)
        check("面板沿用原值，未被污染", stored["scores"]["mind"] == 1.0,
              json.dumps(stored["scores"], ensure_ascii=False))

    elif a.expect == "empty":
        check("流为空时自动降级为普通调用，回复依然送达", len(text) > 30, f"{len(text)} 字")
        check("未推送 error 事件", "error" not in events)
        check("state.ok = true", events.get("state", {}).get("ok") is True)

    elif a.expect == "refuse":
        check("鉴权失败时向前端推送 error 事件", "error" in events,
              (events.get("error") or {}).get("message", "")[:60])
        check("error 消息包含关键字", "401" in (events.get("error") or {}).get("message", "")
              or "Authentication" in (events.get("error") or {}).get("detail", ""))
        check("同时推送 done.ok = false", events.get("done", {}).get("ok") is False)

    call("DELETE", f"/api/sessions/{sid}")
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"无法连接 {BASE}：{exc}")
        sys.exit(2)
