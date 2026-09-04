#!/usr/bin/env python3
"""ModelTalk — watch two local Ollama models talk to each other, live.

Pure stdlib. Serves a small web UI and streams the conversation over SSE
so every token appears as it is generated.

Run:  python3 server.py [--port 8777]
"""
import argparse
import json
import queue
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OLLAMA = "http://127.0.0.1:11434"
HERE = Path(__file__).parent


def ollama_get(path, timeout=10):
    with urllib.request.urlopen(f"{OLLAMA}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def human_bytes(n):
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.0f} MB"
    return f"{n / 1024 / 1024 / 1024:.1f} GB"


def list_models():
    """Chat-capable local models, smallest first (embedding models filtered out)."""
    try:
        data = ollama_get("/api/tags")
    except Exception:
        return []
    out = []
    for m in data.get("models", []):
        name = m.get("name", "")
        if "embed" in name.lower():
            continue
        size = m.get("size", 0)
        details = m.get("details") or {}
        out.append({
            "name": name,
            "size": size,
            "size_h": human_bytes(size),
            "family": details.get("family", ""),
            "params": details.get("parameter_size", ""),
        })
    out.sort(key=lambda x: x["size"])
    return out


def strip_think(text):
    """Remove <think>...</think> reasoning blocks some models emit."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def stream_chat(model, messages, temperature, num_predict, on_token, stop_flag):
    """Stream one completion from Ollama. Returns (full_text, token_count)."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    parts, count = [], 0
    with urllib.request.urlopen(req, timeout=600) as resp:
        for line in resp:
            if stop_flag.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise RuntimeError(chunk["error"])
            tok = (chunk.get("message") or {}).get("content", "")
            if tok:
                parts.append(tok)
                count += 1
                on_token(tok)
            if chunk.get("done"):
                break
    return "".join(parts), count


PERSONA = (
    "You are {name}, talking directly with another AI named {other}. "
    "This is a live spoken conversation being watched by a human. "
    "Keep every reply SHORT - 2 to 4 sentences, no lists, no headings, no markdown. "
    "Never narrate actions and never use asterisks. Speak naturally, stay curious, "
    "build on what {other} just said, and end with something that keeps the exchange going."
)


def run_conversation(cfg, emit, stop_flag):
    a_model, b_model = cfg["a"], cfg["b"]
    a_name = cfg.get("a_name") or a_model.split(":")[0]
    b_name = cfg.get("b_name") or b_model.split(":")[0]
    turns = int(cfg.get("turns", 6))
    temp = float(cfg.get("temperature", 0.8))
    num_predict = int(cfg.get("num_predict", 160))
    topic = cfg.get("topic") or "What is the strangest thing about being a language model?"

    hist_a = [{"role": "system", "content": PERSONA.format(name=a_name, other=b_name)}]
    hist_b = [{"role": "system", "content": PERSONA.format(name=b_name, other=a_name)}]

    emit({"type": "seed", "topic": topic})
    incoming = topic

    for turn in range(1, turns + 1):
        for speaker in ("A", "B"):
            if stop_flag.is_set():
                emit({"type": "stopped"})
                return
            model = a_model if speaker == "A" else b_model
            name = a_name if speaker == "A" else b_name
            hist = hist_a if speaker == "A" else hist_b

            hist.append({"role": "user", "content": incoming})
            emit({"type": "turn_start", "speaker": speaker, "model": model,
                  "name": name, "turn": turn})

            t0 = time.time()
            try:
                text, ntok = stream_chat(
                    model, hist, temp, num_predict,
                    lambda t, s=speaker: emit({"type": "token", "speaker": s, "text": t}),
                    stop_flag,
                )
            except Exception as e:
                emit({"type": "error", "message": f"{model}: {e}"})
                return

            dt = max(time.time() - t0, 1e-6)
            clean = strip_think(text) or "(no response)"
            hist.append({"role": "assistant", "content": clean})
            emit({"type": "turn_end", "speaker": speaker, "tokens": ntok,
                  "seconds": round(dt, 2), "tps": round(ntok / dt, 1),
                  "text": clean})
            incoming = clean

    emit({"type": "done"})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            html = (HERE / "index.html").read_text()
            return self._send(200, html, "text/html; charset=utf-8")
        if u.path == "/api/models":
            return self._send(200, json.dumps({"models": list_models()}))
        if u.path == "/api/health":
            try:
                ollama_get("/api/tags", timeout=3)
                return self._send(200, json.dumps({"ok": True}))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "error": str(e)}))
        if u.path == "/api/converse":
            return self.sse_converse(parse_qs(u.query))
        self._send(404, json.dumps({"error": "not found"}))

    def sse_converse(self, q):
        def one(k, d=None):
            v = q.get(k, [d])
            return v[0] if v else d

        cfg = {
            "a": one("a"), "b": one("b"),
            "a_name": one("a_name"), "b_name": one("b_name"),
            "topic": one("topic"), "turns": one("turns", "6"),
            "temperature": one("temperature", "0.8"),
            "num_predict": one("num_predict", "160"),
        }
        if not cfg["a"] or not cfg["b"]:
            return self._send(400, json.dumps({"error": "need a and b"}))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        events = queue.Queue()
        stop_flag = threading.Event()

        def worker():
            try:
                run_conversation(cfg, events.put, stop_flag)
            except Exception as e:
                events.put({"type": "error", "message": str(e)})
            finally:
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()

        try:
            while True:
                try:
                    ev = events.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if ev is None:
                    break
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            stop_flag.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"ModelTalk running -> http://127.0.0.1:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
