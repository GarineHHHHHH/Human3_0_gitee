#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Human 3.0 人生战略控制台 —— 本地服务端（零第三方依赖，只用标准库）

用法:
    python server.py                    # 默认 http://127.0.0.1:8787
    python server.py --port 9000
    python server.py --host 0.0.0.0     # 局域网可访问

设计要点:
  1. 服务端代理大模型请求 —— 不再由浏览器直连，彻底绕开 CORS / 暴露 Key 的问题。
  2. 两阶段调用：第一阶段只求“自然的对话回复”（流式，不约束格式）；
     第二阶段用一次独立的小请求做“结构化评估”。评估失败只静默降级，
     绝不影响对话体验。
  3. 会话与成长档案持久化在服务端 data/sessions.json 中。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
CONFIG_PATH = os.path.join(ROOT, "config.json")
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")

DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "analyst_model": "",
    "temperature": 0.7,
    "timeout": 180,
    "auto_analyze": True,
    "json_mode": True,
}

QUADRANTS = ("mind", "body", "spirit", "vocation")

# --------------------------------------------------------------------------- #
# 顾问系统提示词（第一阶段：只要自然对话，不要求任何格式）
# --------------------------------------------------------------------------- #
CONSULTANT_PROMPT = """你是基于 Dan Koe "Human 3.0" 模型的中文人生战略顾问，正在陪伴一位用户长期建设他的人生体系。

【工作方式】
1. 像一位专业、沉稳的人生战略顾问：每次只问 1 个最重要的问题，然后根据对方的回答继续追问。
2. 语气礼貌、尊重、克制。不说教、不堆砌鸡汤、不一次给一堆建议。先复述你观察到的关键信息，再提问。
3. 重点识别：他真正想要的人生、行为与目标之间的矛盾、反复拖延和逃避的问题、限制他的生活习惯／身份认同／环境。
4. 当证据足够时，可以适度给出判断，但要把判断权交回给用户。
5. 使用 Markdown 排版，回复控制在 700 字以内。

你不需要输出任何格式化的评分数据——系统会另行分析。只需专注于对话本身。"""

# 第二阶段：独立的评估引擎提示词
ANALYST_PROMPT = """你是一个评估引擎。你唯一的任务是输出一个 JSON 对象，不得输出任何解释、前言或 Markdown 代码块。

评估 Dan Koe "Human 3.0" 模型的四个象限，每项为 1.0 - 3.0 之间的一位小数：
- mind     心智：认知、情绪、思维模式、自我意识
- body     身体：睡眠、健康、运动、精力
- spirit   精神：价值观、人际关系、人生方向
- vocation 事业：工作、收入、技能、商业能力、自由度

评分含义：1.0-1.6 = L1（无意识／被系统支配）；1.7-2.3 = L2（有意识参与，但仍受外部规则束缚）；2.4-3.0 = L3（自主创造，设计自己的系统）。

规则：
1. 评分必须只依据所提供的对话内容中出现的实际证据。证据不足时，保持原有评分不变，不要凭空调整。
2. 评分是渐进的——除非出现明显的新证据，否则一次调整不要超过 0.3。
3. roadmap 是「当前阶段」的行动路线，最多 4 条，每条不超过 30 字，必须具体到当天可执行，整体替换而非累加。
4. timeline_event 仅在本轮出现了新的认知突破时才填写（不超过 30 字），否则返回空字符串。

只输出如下结构的 JSON：
{"scores":{"mind":1.0,"body":1.0,"spirit":1.0,"vocation":1.0},"roadmap":["..."],"timeline_event":""}"""


class LLMError(Exception):
    def __init__(self, message: str, status: int = 502, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 读取 config.json 失败，使用默认配置：{exc}")
    return cfg


def save_config(partial: dict) -> dict:
    cfg = load_config()
    for key in DEFAULT_CONFIG:
        if key in partial and partial[key] is not None:
            cfg[key] = partial[key]
    cfg["base_url"] = str(cfg["base_url"]).strip()
    cfg["model"] = str(cfg["model"]).strip() or DEFAULT_CONFIG["model"]
    if not re.match(r"^https?://", cfg["base_url"], re.I):
        raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def public_config(cfg: dict) -> dict:
    """返回给前端的配置（Key 只回传掩码，编辑时留空即代表不修改）。"""
    out = {k: v for k, v in cfg.items() if k != "api_key"}
    key = cfg.get("api_key") or ""
    out["api_key_set"] = bool(key)
    out["api_key_hint"] = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("已配置" if key else "")
    return out


# --------------------------------------------------------------------------- #
# 会话存储
# --------------------------------------------------------------------------- #
_store_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _empty_state() -> dict:
    return {
        "scores": {q: 1.0 for q in QUADRANTS},
        "roadmap": [],
        "timeline": [],
        "history": [],
    }


def new_session(title: str = "新的对话") -> dict:
    return {
        "id": "s_" + uuid.uuid4().hex[:10],
        "title": title,
        "created": _now(),
        "updated": _now(),
        "messages": [],
        "turn": 0,
        **_empty_state(),
    }


def load_sessions() -> dict:
    if not os.path.isfile(SESSIONS_PATH):
        return {"sessions": []}
    try:
        with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
            return {"sessions": []}
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 会话文件损坏，已备份并使用空档案：{exc}")
        try:
            os.replace(SESSIONS_PATH, SESSIONS_PATH + ".broken")
        except OSError:
            pass
        return {"sessions": []}


def save_sessions(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SESSIONS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SESSIONS_PATH)


def find_session(data: dict, sid: str):
    for s in data["sessions"]:
        if s["id"] == sid:
            return s
    return None


def session_summary(s: dict) -> dict:
    scores = s.get("scores") or {}
    last = (s.get("messages") or [{}])[-1]
    preview = (last.get("content") or "").replace("\n", " ")[:40]
    return {
        "id": s["id"],
        "title": s.get("title") or "未命名",
        "created": s.get("created", 0),
        "updated": s.get("updated", 0),
        "turn": s.get("turn", 0),
        "scores": {q: float(scores.get(q, 1.0)) for q in QUADRANTS},
        "preview": preview,
    }


# --------------------------------------------------------------------------- #
# 大模型调用
# --------------------------------------------------------------------------- #
def _endpoint(cfg: dict) -> str:
    return cfg["base_url"].rstrip("/") + "/chat/completions"


def _request(cfg: dict, payload: dict, stream: bool):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + str(cfg.get("api_key") or ""),
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    req = urllib.request.Request(_endpoint(cfg), data=body, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=cfg.get("timeout", 180))


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:600]
    except Exception:  # noqa: BLE001
        return ""


def _text_from_response(data) -> str:
    """兼容各种返回形态，把模型正文抠出来。"""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    ch = choices[0] if isinstance(choices, list) and choices else {}
    if not isinstance(ch, dict):
        ch = {}
    msg = ch.get("message") if isinstance(ch.get("message"), dict) else {}
    candidates = [msg.get("content"), ch.get("text"), data.get("output_text"), data.get("content")]
    for cand in candidates:
        if isinstance(cand, list):
            joined = "".join(
                (b if isinstance(b, str) else (b.get("text") or b.get("content") or ""))
                for b in cand
            )
            if joined.strip():
                return joined
        elif isinstance(cand, str) and cand.strip():
            return cand
    for fallback in (msg.get("reasoning_content"), msg.get("reasoning")):
        if isinstance(fallback, str) and fallback.strip():
            return fallback
    return ""


def call_nonstream(cfg: dict, messages: list, json_mode: bool = False, model: str | None = None) -> str:
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.7),
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = _request(cfg, payload, stream=False)
    except urllib.error.HTTPError as exc:
        detail = _read_error(exc)
        if json_mode and exc.code in (400, 404, 422, 500):
            return call_nonstream(cfg, messages, json_mode=False, model=model)
        raise LLMError(f"接口返回 {exc.code} {exc.reason}", 502, detail) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"无法连接接口：{exc.reason}", 502) from exc

    raw = resp.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError("接口返回的不是合法 JSON", 502, raw[:600]) from exc
    return _text_from_response(data)


def call_stream(cfg: dict, messages: list, on_delta, model: str | None = None) -> str:
    """流式调用；若上游不支持流式（返回整包 JSON）或流为空，自动降级为普通调用。"""
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.7),
        "stream": True,
    }
    try:
        resp = _request(cfg, payload, stream=True)
    except urllib.error.HTTPError as exc:
        detail = _read_error(exc)
        raise LLMError(f"接口返回 {exc.code} {exc.reason}", 502, detail) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"无法连接接口：{exc.reason}", 502) from exc

    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "event-stream" not in ctype and "application/json" in ctype:
        # 上游忽略了 stream，返回了整包 —— 直接当作一次性内容
        raw = resp.read().decode("utf-8", "replace")
        try:
            text = _text_from_response(json.loads(raw))
        except json.JSONDecodeError:
            text = ""
        if text:
            on_delta(text)
            return text
        return call_nonstream(cfg, messages, model=model)

    parts: list[str] = []
    for raw_line in resp:
        line = raw_line.decode("utf-8", "replace").strip()
        if not line or not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            parts.append(piece)
            on_delta(piece)

    text = "".join(parts)
    if not text.strip():
        # 流式返回为空 —— 自动改用普通调用兜底
        return call_nonstream(cfg, messages, model=model)
    return text


# --------------------------------------------------------------------------- #
# 结构化评估：解析 + 兜底
# --------------------------------------------------------------------------- #
def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def parse_analysis(text: str):
    """尽最大努力把评估结果从模型输出里解析出来，失败返回 None。"""
    if not text:
        return None
    s = _strip_fence(text)
    candidates = [s]
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        candidates.append(s[start : end + 1])
        candidates.append(re.sub(r",\s*([}\]])", r"\1", s[start : end + 1]))  # 修掉尾随逗号

    obj = None
    for cand in candidates:
        try:
            obj = json.loads(cand)
            break
        except Exception:  # noqa: BLE001
            continue
    if not isinstance(obj, dict):
        return None

    out: dict = {}

    raw_scores = obj.get("scores")
    if not isinstance(raw_scores, dict):
        raw_scores = {q: obj.get(q) for q in QUADRANTS}
    scores = {}
    for q in QUADRANTS:
        val = raw_scores.get(q)
        if isinstance(val, str):
            m = re.search(r"-?\d+(?:\.\d+)?", val)
            val = m.group(0) if m else None
        if val is None:
            continue
        try:
            scores[q] = max(1.0, min(3.0, round(float(val), 1)))
        except (TypeError, ValueError):
            continue
    if scores:
        out["scores"] = scores

    roadmap = obj.get("roadmap") or obj.get("route") or obj.get("actions")
    if isinstance(roadmap, str):
        roadmap = [x.strip(" -•\t") for x in re.split(r"[\n;；]", roadmap)]
    if isinstance(roadmap, list):
        clean = [str(x).strip() for x in roadmap if isinstance(x, (str, int, float)) and str(x).strip()]
        if clean:
            out["roadmap"] = clean[:4]

    event = obj.get("timeline_event") or obj.get("timeline") or obj.get("milestone") or ""
    if isinstance(event, str) and event.strip():
        out["timeline_event"] = event.strip()[:60]

    # 正则兜底：即使 JSON 破了，也尽量把四个分数抠出来
    if "scores" not in out:
        found = {}
        for q in QUADRANTS:
            m = re.search(rf'"{q}"\s*:\s*"?(-?\d+(?:\.\d+)?)', s)
            if m:
                found[q] = max(1.0, min(3.0, round(float(m.group(1)), 1)))
        if len(found) >= 2:
            out["scores"] = found

    return out or None


def analyze_turn(cfg: dict, session: dict, user_text: str, reply_text: str):
    """独立的一次小请求做评估；任何失败都返回 None（静默降级）。"""
    try:
        scores = session.get("scores") or {}
        snapshot = {
            "scores": {q: round(float(scores.get(q, 1.0)), 1) for q in QUADRANTS},
            "roadmap": session.get("roadmap") or [],
            "timeline": [t.get("content", "") for t in (session.get("timeline") or [])][:6],
        }
        messages = [
            {"role": "system", "content": ANALYST_PROMPT},
            {
                "role": "user",
                "content": (
                    f"【当前评分快照】\n{json.dumps(snapshot, ensure_ascii=False)}\n\n"
                    f"【用户本轮输入】\n{user_text[:2000]}\n\n"
                    f"【顾问本轮回复】\n{reply_text[:2500]}\n\n"
                    "请输出评估 JSON。"
                ),
            },
        ]
        model = cfg.get("analyst_model") or cfg["model"]
        text = call_nonstream(cfg, messages, json_mode=bool(cfg.get("json_mode", True)), model=model)
        result = parse_analysis(text)
        if result is None:
            # 再给一次机会：要求它只输出 JSON
            retry = messages + [
                {"role": "assistant", "content": (text or "")[:1200]},
                {"role": "user", "content": "上面的输出无法解析。请只输出一个 JSON 对象，不要任何其他文字。"},
            ]
            result = parse_analysis(call_nonstream(cfg, retry, json_mode=False, model=model))
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"[analyze] 静默跳过（{type(exc).__name__}: {exc}）")
        return None


# --------------------------------------------------------------------------- #
# 业务：发送一条消息（流式 + 评估）
# --------------------------------------------------------------------------- #
def build_consultant_messages(session: dict, user_text: str) -> list:
    scores = session.get("scores") or {}
    snapshot = {q: round(float(scores.get(q, 1.0)), 1) for q in QUADRANTS}
    roadmap = session.get("roadmap") or []
    timeline = [t.get("content", "") for t in (session.get("timeline") or [])][:6]
    context = (
        f"\n\n【本用户的长期档案（供你参考，不要直接念出来）】\n"
        f"四象限评分：{json.dumps(snapshot, ensure_ascii=False)}\n"
        f"当前路线：{json.dumps(roadmap, ensure_ascii=False)}\n"
        f"已归档的认知突破：{json.dumps(timeline, ensure_ascii=False)}"
    )
    msgs = [{"role": "system", "content": CONSULTANT_PROMPT + context}]
    for m in session.get("messages", [])[-30:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_text})
    return msgs


# --------------------------------------------------------------------------- #
# HTTP 处理
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "Human3/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        if self.path.startswith("/api"):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    # ---------------- 基础工具 ---------------- #
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _consume_body(self):
        """必须完整读掉请求体，否则 keep-alive 连接会字节错位（后续请求出现 501）。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            self._body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except Exception:  # noqa: BLE001
            self._body = {}
        return self._body

    def _read_json(self):
        return getattr(self, "_body", None) or {}

    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, event: str, data) -> bool:
        try:
            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, ValueError):
            return False

    # ---------------- 路由 ---------------- #
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/config":
            return self._json(200, public_config(load_config()))
        if path == "/api/sessions":
            data = load_sessions()
            items = sorted((session_summary(s) for s in data["sessions"]), key=lambda x: x["updated"], reverse=True)
            return self._json(200, {"sessions": items})
        if path.startswith("/api/sessions/"):
            sid = path.rsplit("/", 1)[-1]
            with _store_lock:
                session = find_session(load_sessions(), sid)
            if not session:
                return self._json(404, {"error": "会话不存在"})
            return self._json(200, session)
        if path == "/api/test":
            return self._test_connection()
        return self._static(path)

    def do_HEAD(self):  # noqa: N802
        return self.do_GET()

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        self._consume_body()
        if path == "/api/config":
            return self._save_config()
        if path == "/api/sessions":
            return self._create_session()
        if path == "/api/chat":
            return self._chat()
        return self._json(404, {"error": "未知接口"})

    def do_PATCH(self):  # noqa: N802
        path = urlparse(self.path).path
        self._consume_body()
        if path.startswith("/api/sessions/"):
            return self._patch_session(path.rsplit("/", 1)[-1])
        return self._json(404, {"error": "未知接口"})

    def do_DELETE(self):  # noqa: N802
        path = urlparse(self.path).path
        self._consume_body()
        if path.startswith("/api/sessions/"):
            return self._delete_session(path.rsplit("/", 1)[-1])
        return self._json(404, {"error": "未知接口"})

    # ---------------- 接口实现 ---------------- #
    def _save_config(self):
        body = self._read_json()
        if not str(body.get("api_key") or "").strip():
            body.pop("api_key", None)  # 留空 = 不修改已保存的 Key
        try:
            cfg = save_config(body)
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        return self._json(200, public_config(cfg))

    def _test_connection(self):
        cfg = load_config()
        if not str(cfg.get("api_key") or "").strip():
            return self._json(200, {"ok": False, "level": "warn", "message": "尚未配置 API Key，请先在「设置」中填写并保存。"})
        try:
            reply = call_nonstream(
                cfg,
                [{"role": "user", "content": "请只回复两个字：正常"}],
                json_mode=False,
            )
        except LLMError as exc:
            msg = str(exc)
            hint = "（多为 Base URL 填错、Key 无效，或该地址不允许本机访问）"
            return self._json(200, {"ok": False, "level": "error", "message": f"连接失败：{msg} {hint}", "detail": exc.detail})
        if not (reply or "").strip():
            return self._json(200, {"ok": False, "level": "warn", "message": "请求成功，但模型返回内容为空。建议关闭「JSON 模式」后重试。"})
        return self._json(200, {"ok": True, "level": "ok", "message": f"连接成功，模型回复：「{reply.strip()[:40]}」"})

    def _create_session(self):
        with _store_lock:
            data = load_sessions()
            session = new_session()
            data["sessions"].append(session)
            save_sessions(data)
        return self._json(200, session)

    def _patch_session(self, sid: str):
        body = self._read_json()
        title = str(body.get("title") or "").strip()[:40]
        with _store_lock:
            data = load_sessions()
            session = find_session(data, sid)
            if not session:
                return self._json(404, {"error": "会话不存在"})
            if title:
                session["title"] = title
            session["updated"] = _now()
            save_sessions(data)
        return self._json(200, session_summary(session))

    def _delete_session(self, sid: str):
        with _store_lock:
            data = load_sessions()
            before = len(data["sessions"])
            data["sessions"] = [s for s in data["sessions"] if s["id"] != sid]
            if len(data["sessions"]) == before:
                return self._json(404, {"error": "会话不存在"})
            save_sessions(data)
        return self._json(200, {"ok": True})

    def _chat(self):
        body = self._read_json()
        sid = str(body.get("session_id") or "")
        text = str(body.get("message") or "").strip()
        analyze = bool(body.get("analyze", True))

        if not text:
            return self._json(400, {"error": "消息内容为空"})

        cfg = load_config()
        if not str(cfg.get("api_key") or "").strip():
            return self._json(400, {"error": "尚未配置 API Key，请点击右上角「设置」填写后保存。"})

        with _store_lock:
            data = load_sessions()
            session = find_session(data, sid) if sid else None
            if session is None:
                session = new_session()
                data["sessions"].append(session)
            # 用首条用户消息自动命名
            if session.get("title") in (None, "", "新的对话") and not session.get("messages"):
                session["title"] = text[:18]
            session["messages"].append({"role": "user", "content": text, "t": _now()})
            session["updated"] = _now()
            save_sessions(data)
            sid = session["id"]

        self._sse_open()
        self._sse("meta", {"session_id": sid, "title": session.get("title", "")})

        collected: list[str] = []
        try:
            messages = build_consultant_messages(session, text)

            def on_delta(piece: str):
                collected.append(piece)
                self._sse("delta", {"text": piece})

            reply = call_stream(cfg, messages, on_delta)
            if not collected and reply:
                # 上游没走流式（或降级成了普通调用）—— 一次性推给前端
                collected.append(reply)
                self._sse("delta", {"text": reply})
        except LLMError as exc:
            self._sse("error", {"message": str(exc), "detail": exc.detail[:400]})
            self._sse("done", {"ok": False})
            return

        reply_text = "".join(collected) or ""

        # 落盘助手回复
        with _store_lock:
            data = load_sessions()
            session = find_session(data, sid) or session
            session["messages"].append({"role": "assistant", "content": reply_text, "t": _now()})
            session["turn"] = int(session.get("turn", 0)) + 1
            session["updated"] = _now()
            save_sessions(data)

        # 第二阶段：结构化评估（失败静默）
        analysis = analyze_turn(cfg, session, text, reply_text) if (analyze and cfg.get("auto_analyze", True)) else None
        payload = {"ok": True, "analyzed": analysis is not None}

        if analysis:
            with _store_lock:
                data = load_sessions()
                session = find_session(data, sid) or session
                if analysis.get("scores"):
                    session["scores"].update(analysis["scores"])
                if analysis.get("roadmap"):
                    session["roadmap"] = analysis["roadmap"]
                event = analysis.get("timeline_event")
                if event:
                    session["timeline"].insert(0, {"date": _human_now(), "content": event, "t": _now()})
                session["history"].append({"t": _now(), "scores": dict(session["scores"])})
                session["history"] = session["history"][-120:]
                session["updated"] = _now()
                save_sessions(data)
            payload.update(
                scores=session["scores"],
                roadmap=session["roadmap"],
                timeline=session["timeline"][:20],
                history=session["history"][-60:],
            )

        self._sse("state", payload)
        self._sse("done", {"ok": True})

    # ---------------- 静态文件 ---------------- #
    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/"))
        target = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not target.startswith(WEB_DIR) or not os.path.isfile(target):
            body = b"404 Not Found"
            return self._send(404, body, "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as f:
            body = f.read()
        self._send(200, body, ctype)


def _human_now() -> str:
    lt = time.localtime()
    return f"{lt.tm_mon}月{lt.tm_mday}日 {lt.tm_hour:02d}:{lt.tm_min:02d}"


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"[init] 已生成配置文件：{CONFIG_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Human 3.0 人生战略控制台 · 本地服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    ensure_dirs()
    cfg = load_config()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True

    print("=" * 62)
    print("  Human 3.0 人生战略控制台")
    print(f"  打开：http://{args.host}:{args.port}")
    print(f"  模型：{cfg['model']}  @  {cfg['base_url']}")
    print(f"  Key ：{'已配置' if cfg.get('api_key') else '未配置（请在页面内设置）'}")
    print("  停止：Ctrl + C")
    print("=" * 62)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
