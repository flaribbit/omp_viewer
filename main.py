from __future__ import annotations

import argparse
import json
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


SESSIONS_DIR = Path.home() / ".omp" / "agent" / "sessions"


def read_json_lines(path: Path):
    """Yield valid JSON objects from a session JSONL file."""
    try:
        with path.open(encoding="utf-8") as session_file:
            for line in session_file:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def session_summary(path: Path) -> dict[str, str]:
    title = ""
    timestamp = ""

    for record in read_json_lines(path):
        if record.get("type") == "title" and isinstance(record.get("title"), str):
            title = record["title"]
        elif record.get("type") == "session":
            if not title and isinstance(record.get("title"), str):
                title = record["title"]
            if isinstance(record.get("timestamp"), str):
                timestamp = record["timestamp"]
        if title and timestamp:
            break

    if not timestamp:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()

    return {
        "title": title or path.stem,
        "timestamp": timestamp,
    }


def list_sessions() -> list[dict[str, object]]:
    if not SESSIONS_DIR.is_dir():
        return []

    groups: list[dict[str, object]] = []
    for directory in sorted((item for item in SESSIONS_DIR.iterdir() if item.is_dir()), key=lambda item: item.name):
        sessions = []
        for path in directory.glob("*.jsonl"):
            summary = session_summary(path)
            sessions.append(
                {
                    "path": path.relative_to(SESSIONS_DIR).as_posix(),
                    **summary,
                }
            )
        if sessions:
            sessions.sort(key=lambda session: str(session["timestamp"]), reverse=True)
            groups.append({"name": directory.name, "sessions": sessions})
    return groups


def text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def read_session(relative_path: str) -> list[dict[str, str]] | None:
    requested = Path(relative_path)
    if requested.is_absolute() or requested.suffix != ".jsonl":
        return None

    try:
        path = (SESSIONS_DIR / requested).resolve()
        path.relative_to(SESSIONS_DIR.resolve())
    except (OSError, ValueError):
        return None

    if not path.is_file():
        return None

    messages = []
    for record in read_json_lines(path):
        if record.get("type") != "message":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = text_content(message.get("content"))
        if not text:
            continue
        messages.append(
            {
                "role": role,
                "text": text,
                "timestamp": record.get("timestamp", ""),
            }
        )
    return messages


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Oh My Pi 对话历史</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css">
  <style>
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; overflow: hidden; }
    body { font-family: sans-serif; color: #111; }
    a { color: inherit; }
    button { font: inherit; }
    #app { display: grid; grid-template-columns: 18rem minmax(0, 1fr); height: 100vh; min-height: 0; }
    #sessions { border-right: 1px solid #bbb; min-height: 0; overflow-y: auto; padding: 1rem; }
    #sessions h1 { font-size: 1rem; margin: 0 0 1rem; }
    .group { margin: 0 0 1.25rem; }
    .group h2 { font-size: .875rem; margin: 0 0 .5rem; overflow-wrap: anywhere; }
    .session-link { display: block; margin: .35rem 0; overflow-wrap: anywhere; }
    .session-link[aria-current="page"] { font-weight: bold; }
    #viewer { display: grid; grid-template-columns: 14rem minmax(0, 1fr); min-width: 0; min-height: 0; overflow: hidden; }
    #toc { border-right: 1px solid #bbb; min-height: 0; overflow-y: auto; padding: 1rem; }
    #toc h2 { font-size: 1rem; margin: 0 0 1rem; }
    .toc-link { display: block; margin: .4rem 0; overflow-wrap: anywhere; }
    #messages { min-height: 0; overflow-y: auto; padding: 1rem 2rem; scroll-behavior: smooth; }
    .message { border-bottom: 1px solid #ddd; padding: 0 0 1.25rem; margin: 0 0 1.25rem; }
    .message-header { display: flex; gap: .75rem; align-items: baseline; margin-bottom: .75rem; }
    .role { font-weight: bold; }
    .timestamp { color: #666; font-size: .8rem; }
    .message-body { overflow-wrap: anywhere; }
    .message-body pre { overflow-x: auto; }
    .copy { margin-top: .75rem; }
    .status { color: #555; }
    @media (max-width: 800px) {
      #app { grid-template-columns: 12rem minmax(0, 1fr); }
      #viewer { grid-template-columns: 10rem minmax(0, 1fr); }
      #messages { padding: 1rem; }
    }
  </style>
</head>
<body>
  <main id="app">
    <nav id="sessions" aria-label="会话列表"><h1>会话</h1><p class="status">加载中…</p></nav>
    <section id="viewer">
      <nav id="toc" aria-label="用户输入目录"><h2>目录</h2><p class="status">选择一个会话。</p></nav>
      <article id="messages" aria-live="polite"><p class="status">选择一个会话。</p></article>
    </section>
  </main>
  <script src="https://cdn.jsdelivr.net/npm/marked@15.0.12/lib/marked.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/contrib/auto-render.min.js"></script>
  <script>
    const sessionsElement = document.querySelector('#sessions');
    const tocElement = document.querySelector('#toc');
    const messagesElement = document.querySelector('#messages');
    let knownSessions = new Set();
    let requestVersion = 0;

    function selectedPath() {
      const match = location.hash.match(/^#session=(.*)$/);
      if (!match) return null;
      try { return decodeURIComponent(match[1]); } catch { return null; }
    }

    async function fetchJson(url) {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) throw new Error(`请求失败：${response.status}`);
      return response.json();
    }

    function clearAndStatus(element, text) {
      element.replaceChildren();
      const status = document.createElement('p');
      status.className = 'status';
      status.textContent = text;
      element.append(status);
    }

    function shortPrompt(text) {
      const compact = text.trim().replace(/\s+/g, ' ');
      return compact.length > 10 ? `${compact.slice(0, 10)}…` : compact || '（空输入）';
    }

    function renderMarkdown(element, markdown) {
      const renderer = new marked.Renderer();
      renderer.html = ({ text }) => String(text).replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      })[character]);
      element.innerHTML = marked.parse(markdown, { renderer, gfm: true });
      renderMathInElement(element, {
        delimiters: [
          { left: '\\(', right: '\\)', display: false },
          { left: '\\[', right: '\\]', display: true }
        ],
        throwOnError: false
      });
    }

    async function copyMarkdown(button, text) {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const fallback = document.createElement('textarea');
        fallback.value = text;
        fallback.style.position = 'fixed';
        fallback.style.opacity = '0';
        document.body.append(fallback);
        fallback.select();
        document.execCommand('copy');
        fallback.remove();
      }
      const originalLabel = button.textContent;
      button.textContent = '已复制';
      setTimeout(() => { button.textContent = originalLabel; }, 1200);
    }

    function renderConversation(messages) {
      tocElement.replaceChildren();
      messagesElement.replaceChildren();
      const heading = document.createElement('h2');
      heading.textContent = '目录';
      tocElement.append(heading);

      if (!messages.length) {
        clearAndStatus(messagesElement, '这个会话没有可显示的文本对话。');
        return;
      }

      let userCount = 0;
      messages.forEach((message, index) => {
        const id = `message-${index}`;
        const messageElement = document.createElement('section');
        messageElement.className = 'message';
        messageElement.id = id;

        const header = document.createElement('div');
        header.className = 'message-header';
        const role = document.createElement('span');
        role.className = 'role';
        role.textContent = message.role === 'user' ? '用户' : '助手';
        header.append(role);
        if (message.timestamp) {
          const timestamp = document.createElement('time');
          timestamp.className = 'timestamp';
          timestamp.textContent = message.timestamp;
          header.append(timestamp);
        }

        const body = document.createElement('div');
        body.className = 'message-body';
        renderMarkdown(body, message.text);

        const copy = document.createElement('button');
        copy.className = 'copy';
        copy.type = 'button';
        copy.textContent = '复制 Markdown';
        copy.addEventListener('click', () => copyMarkdown(copy, message.text));

        messageElement.append(header, body, copy);
        messagesElement.append(messageElement);

        if (message.role === 'user') {
          userCount += 1;
          const link = document.createElement('a');
          link.className = 'toc-link';
          link.href = `#${id}`;
          link.textContent = shortPrompt(message.text);
          link.addEventListener('click', event => {
            event.preventDefault();
            document.getElementById(id).scrollIntoView({ behavior: 'smooth', block: 'start' });
          });
          tocElement.append(link);
        }
      });

      if (!userCount) {
        const empty = document.createElement('p');
        empty.className = 'status';
        empty.textContent = '没有用户输入。';
        tocElement.append(empty);
      }
    }

    async function loadSelectedSession() {
      const path = selectedPath();
      if (!path) {
        clearAndStatus(tocElement, '选择一个会话。');
        clearAndStatus(messagesElement, '选择一个会话。');
        return;
      }
      if (!knownSessions.has(path)) {
        clearAndStatus(tocElement, '会话不存在。');
        clearAndStatus(messagesElement, '会话不存在或已被删除。');
        return;
      }

      const version = ++requestVersion;
      clearAndStatus(tocElement, '加载中…');
      clearAndStatus(messagesElement, '加载中…');
      try {
        const data = await fetchJson(`/api/session?path=${encodeURIComponent(path)}`);
        if (version === requestVersion) renderConversation(data.messages);
      } catch (error) {
        if (version === requestVersion) {
          clearAndStatus(tocElement, '无法加载会话。');
          clearAndStatus(messagesElement, error.message);
        }
      }
    }

    async function loadIndex() {
      try {
        const groups = await fetchJson('/api/sessions');
        knownSessions = new Set();
        sessionsElement.replaceChildren();
        const heading = document.createElement('h1');
        heading.textContent = '会话';
        sessionsElement.append(heading);

        groups.forEach(group => {
          const groupElement = document.createElement('section');
          groupElement.className = 'group';
          const groupHeading = document.createElement('h2');
          groupHeading.textContent = group.name;
          groupElement.append(groupHeading);
          group.sessions.forEach(session => {
            knownSessions.add(session.path);
            const link = document.createElement('a');
            link.className = 'session-link';
            link.href = `#session=${encodeURIComponent(session.path)}`;
            link.textContent = session.title;
            if (session.path === selectedPath()) link.setAttribute('aria-current', 'page');
            groupElement.append(link);
          });
          sessionsElement.append(groupElement);
        });

        if (!groups.length) {
          const status = document.createElement('p');
          status.className = 'status';
          status.textContent = '未找到会话。';
          sessionsElement.append(status);
        }
        loadSelectedSession();
      } catch (error) {
        clearAndStatus(sessionsElement, error.message);
      }
    }

    window.addEventListener('hashchange', () => {
      document.querySelectorAll('.session-link').forEach(link => {
        link.toggleAttribute('aria-current', link.href.endsWith(location.hash));
      });
      loadSelectedSession();
    });
    loadIndex();
  </script>
</body>
</html>
"""


class ViewerHandler(BaseHTTPRequestHandler):
    def send_json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_page(self) -> None:
        body = PAGE.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        request = urlparse(self.path)
        if request.path == "/":
            self.send_page()
        elif request.path == "/api/sessions":
            self.send_json(HTTPStatus.OK, list_sessions())
        elif request.path == "/api/session":
            path = parse_qs(request.query).get("path", [""])[0]
            messages = read_session(unquote(path))
            if messages is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "会话不存在。"})
            else:
                self.send_json(HTTPStatus.OK, {"messages": messages})
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "不存在的路径。"})


def main() -> None:
    global SESSIONS_DIR
    parser = argparse.ArgumentParser(description="查看 Oh My Pi 会话历史")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认：0.0.0.0）")
    parser.add_argument("--port", default=1416, type=int, help="监听端口（默认：1416）")
    parser.add_argument(
        "--sessions-dir",
        default=SESSIONS_DIR,
        type=Path,
        help="会话目录（默认：~/.omp/agent/sessions）",
    )
    args = parser.parse_args()

    SESSIONS_DIR = args.sessions_dir.expanduser().resolve()
    server = ThreadingHTTPServer((args.host, args.port), ViewerHandler)
    print(f"正在监听 http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
