#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端冒烟测试：直接对 server.py 的 HTTP 接口做验证。

用法（先启动 mock_llm.py 与 server.py）:
    python dev/smoke_test.py
    python dev/smoke_test.py --base http://127.0.0.1:8787
"""
import argparse
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8787"
PASS, FAIL = [], []


def call(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def chat(session_id, message, analyze=True):
    """发起一次对话，返回 (deltas, events, final_state)"""
    body = json.dumps({"session_id": session_id, "message": message, "analyze": analyze}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    deltas, events, final = [], {}, None
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
                events[name] = payload
                if name == "delta":
                    deltas.append(payload.get("text", ""))
                if name == "state":
                    final = payload
    return deltas, events, final


def keepalive_probe() -> str:
    """在同一个 socket 上连续发两个请求，验证服务端是否把请求体读干净了。"""
    import socket
    hostport = BASE.replace("http://", "").replace("https://", "").split("/")[0]
    host, _, port = hostport.partition(":")
    port = int(port or 80)
    req = (
        "POST /api/sessions HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 2\r\n\r\n"
        "{}"
        "GET /api/sessions HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Connection: close\r\n\r\n"
    )
    with socket.create_connection((host, port), timeout=15) as s:
        s.sendall(req.encode("utf-8"))
        chunks = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", "replace")


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (f"  → {detail}" if detail else ""))


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    print(f"目标服务: {BASE}\n")

    print("① 基础接口")
    status, cfg = call("GET", "/api/config")
    check("GET /api/config 返回 200", status == 200, f"model={cfg.get('model')}")
    check("Key 以掩码形式返回，不回传明文", "api_key" not in cfg and cfg.get("api_key_set") is True,
          f"hint={cfg.get('api_key_hint')}")

    status, test = call("GET", "/api/test")
    check("GET /api/test 连通", test.get("ok") is True, test.get("message", "")[:50])

    print("\n② 会话与流式对话")
    _, session = call("POST", "/api/sessions", {})
    sid = session["id"]
    check("创建会话", bool(sid))

    deltas, events, final = chat(sid, "我理想中的星期二：早上7点自然醒并晨读英语，下午和心爱的人一起读书摄影。")
    text = "".join(deltas)
    check("收到流式增量", len(deltas) > 0, f"{len(deltas)} 个片段")
    check("增量拼出完整回复", len(text) > 30, f"{len(text)} 字")
    check("收到 meta 事件（回传 session_id）", events.get("meta", {}).get("session_id") == sid)
    check("最终 state.ok = true", final is not None and final.get("ok") is True)
    check("本轮的评估被成功应用", final.get("analyzed") is True)
    if final and final.get("analyzed"):
        check("四象限评分已写入", final.get("scores", {}).get("vocation") not in (None, 1.0),
              json.dumps(final.get("scores"), ensure_ascii=False))
        check("路线建议非空", bool(final.get("roadmap")), "; ".join(final.get("roadmap") or [])[:60])
    check("收到 done 事件", events.get("done", {}).get("ok") is True)

    print("\n③ 持久化")
    _, stored = call("GET", f"/api/sessions/{sid}")
    check("会话已落盘", stored["id"] == sid)
    check("消息成对保存", len(stored["messages"]) == 2, f"{len(stored['messages'])} 条")
    check("用户消息内容正确", "晨读英语" in stored["messages"][0]["content"])
    check("助手消息为纯文本（不含 JSON）", not stored["messages"][1]["content"].lstrip().startswith("{"))
    check("成长曲线已记录 1 个点", len(stored["history"]) == 1)
    check("自动以首条消息命名会话", stored["title"].startswith("我理想中的星期二") or len(stored["title"]) <= 18,
          stored["title"])

    print("\n④ 第二轮：评分应可演进")
    deltas2, events2, final2 = chat(sid, "我发现自己一直把精力放在公司内部考试上，而不是构建自己的副业。")
    check("第二轮流式成功", len(deltas2) > 0)
    check("第二轮评估成功", final2 is not None and final2.get("analyzed") is True)
    if final2 and final2.get("analyzed"):
        check("成长曲线累积 2 个点", len(final2.get("history") or []) == 2,
              json.dumps(final2.get("scores"), ensure_ascii=False))
        check("评分随新证据演进（mind 1.9→2.2）", final2.get("scores", {}).get("mind") == 2.2,
              json.dumps(final2.get("scores"), ensure_ascii=False))
        check("认知档案已归档事件", len(final2.get("timeline") or []) >= 1,
              (final2.get("timeline") or [{}])[0].get("content", ""))

    print("\n⑤ 会话列表与清理")
    _, listing = call("GET", "/api/sessions")
    check("列表包含新会话", any(s["id"] == sid for s in listing["sessions"]))
    call("DELETE", f"/api/sessions/{sid}")
    _, listing2 = call("GET", "/api/sessions")
    check("删除成功", not any(s["id"] == sid for s in listing2["sessions"]))

    print("\n⑥ keep-alive 连接正确性（回归：请求体必须被读掉）")
    raw = keepalive_probe()
    check("同一连接上连续两个请求都被正确响应", "501" not in raw, raw.split("\r\n")[0][:60])
    check("POST 后紧随的 GET 正常返回会话列表", '"sessions"' in raw)

    print(f"\n{'=' * 54}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
