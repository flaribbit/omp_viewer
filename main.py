from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


SESSIONS_DIR = Path.home() / ".omp" / "agent" / "sessions"
UPLOADS_DIR = Path(tempfile.gettempdir()) / "omp"
UPLOAD_SEQUENCE = 0
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


class UploadError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def save_uploaded_image(content_type: str, data: bytes) -> Path:
    content_type = content_type.split(";", 1)[0].strip().lower()
    extension = IMAGE_EXTENSIONS.get(content_type)
    if extension is None:
        raise UploadError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "只支持 PNG、JPEG、GIF、WebP 和 BMP 图片。")
    if not data:
        raise UploadError(HTTPStatus.BAD_REQUEST, "图片内容为空。")
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "单张图片不能超过 20 MB。")

    global UPLOAD_SEQUENCE
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    sequence = UPLOAD_SEQUENCE
    UPLOAD_SEQUENCE = (UPLOAD_SEQUENCE + 1) % 100
    path = UPLOADS_DIR / f"{sequence:02d}{extension}"
    path.write_bytes(data)
    return path




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
    .sidebar-header { display: flex; align-items: center; justify-content: space-between; gap: .5rem; margin-bottom: 1rem; }
    #sessions h2 { margin: 0; }
    .group { margin: 0 0 1.25rem; }
    .group h2 { font-size: .875rem; margin: 0 0 .5rem; overflow-wrap: anywhere; }
    .session-link { display: block; margin: .35rem 0; overflow-wrap: anywhere; }
    .session-link[aria-current] { font-weight: bold; }
    #viewer { display: grid; grid-template-columns: 14rem minmax(0, 1fr); min-width: 0; min-height: 0; overflow: hidden; }
    #toc { border-right: 1px solid #bbb; min-height: 0; overflow-y: auto; padding: 1rem; }
    .toc-header { display: flex; align-items: center; justify-content: space-between; gap: .5rem; margin-bottom: 1rem; }
    #toc h2 { margin: 0; }
    .toc-link { display: block; margin: .4rem 0; overflow-wrap: anywhere; }
    #messages { min-height: 0; overflow-y: auto; padding: 1rem 2rem; scroll-behavior: auto; }
    .message { border-bottom: 1px solid #ddd; padding: 0 0 1.25rem; margin: 0 0 1.25rem; }
    .message-header { display: flex; gap: .75rem; align-items: baseline; margin-bottom: .75rem; }
    .role { font-weight: bold; }
    .timestamp { color: #666; font-size: .8rem; }
    .message-body { min-width: 0; overflow-wrap: anywhere; }
    .message-body p, .message-body ol, .message-body ul { margin-block-start: 0.5em; margin-block-end: 0.5em; }
    .message-body pre, .katex-display { max-width: 100%; overflow-x: auto; overflow-y: hidden; }
    .katex-display > .katex { white-space: nowrap; }
    .copy { margin-top: .75rem; }
    dialog { border: 1px solid #999; border-radius: .35rem; max-width: min(42rem, calc(100vw - 2rem)); width: 100%; padding: 0; }
    dialog::backdrop { background: rgb(0 0 0 / .35); }
    .upload-panel { padding: 1.25rem; }
    .upload-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
    .upload-header h2 { font-size: 1.1rem; margin: 0; }
    .upload-header button { border: 0; background: transparent; cursor: pointer; padding: .25rem; }
    .upload-hint { color: #555; margin: .75rem 0; }
    .upload-dropzone { border: 1px dashed #888; padding: 1rem; text-align: center; cursor: pointer; }
    .upload-dropzone:focus, .upload-dropzone:hover { background: #f5f5f5; }
    .upload-list { display: grid; gap: .6rem; margin: 1rem 0; max-height: 18rem; overflow-y: auto; }
    .upload-item { display: grid; grid-template-columns: 4rem minmax(0, 1fr) auto; gap: .75rem; align-items: center; }
    .upload-preview { width: 4rem; height: 4rem; object-fit: contain; border: 1px solid #ddd; background: #f5f5f5; }
    .upload-name, .upload-path { display: block; overflow-wrap: anywhere; }
    .upload-path { color: #555; font-family: monospace; font-size: .8rem; }
    .upload-remove { padding: .25rem .4rem; white-space: nowrap; }
    .upload-actions { display: flex; justify-content: flex-end; gap: .5rem; }
    .upload-actions button { padding: .4rem .7rem; }
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
    <nav id="sessions" aria-label="会话列表"><div class="sidebar-header"><h2>会话</h2><button id="upload-open" type="button">上传图片</button></div><p class="status">加载中…</p></nav>
    <section id="viewer">
      <nav id="toc" aria-label="用户输入目录"><div class="toc-header"><h2>目录</h2><button id="refresh-sessions" type="button">刷新</button></div><p class="status">选择一个会话。</p></nav>
      <article id="messages" aria-live="polite"><p class="status">选择一个会话。</p></article>
    </section>
  </main>
<dialog id="upload-dialog" aria-labelledby="upload-title">
  <form class="upload-panel">
    <div class="upload-header">
      <h2 id="upload-title">上传图片</h2>
      <button id="upload-close" type="button">关闭</button>
    </div>
    <p class="upload-hint">可以选择图片，也可以把图片复制后在这里按 Ctrl+V；支持一次粘贴多张图片。</p>
    <input id="upload-input" type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/bmp" multiple hidden>
    <div id="upload-dropzone" class="upload-dropzone" role="button" tabindex="0">选择图片</div>
    <div id="upload-list" class="upload-list" aria-live="polite"><p class="status">还没有图片。</p></div>
    <div class="upload-actions">
      <button id="upload-clear" type="button">清空</button>
      <button id="upload-copy" type="button" disabled>复制全部路径</button>
    </div>
  </form>
</dialog>
  <script src="https://cdn.jsdelivr.net/npm/marked@15.0.12/lib/marked.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js"></script>
  <script>
    const sessionsElement = document.querySelector('#sessions');
    const tocElement = document.querySelector('#toc');
    const messagesElement = document.querySelector('#messages');
    let knownSessions = new Set();
    const uploadDialog = document.querySelector('#upload-dialog');
    const uploadOpenButton = document.querySelector('#upload-open');
    const uploadCloseButton = document.querySelector('#upload-close');
    const uploadInput = document.querySelector('#upload-input');
    const uploadDropzone = document.querySelector('#upload-dropzone');
    const uploadList = document.querySelector('#upload-list');
    const uploadClearButton = document.querySelector('#upload-clear');
    const uploadCopyButton = document.querySelector('#upload-copy');
    let uploadItems = [];
    let uploadSequence = 0;
    let requestVersion = 0;
    const mathExtensions = [
      {
        name: 'displayDollarMath',
        level: 'block',
        start(src) { return src.indexOf('$$'); },
        tokenizer(src) {
          if (!src.startsWith('$$')) return;
          const end = src.indexOf('$$', 2);
          if (end < 2) return;
          return { type: 'displayDollarMath', raw: src.slice(0, end + 2), text: src.slice(2, end) };
        },
        renderer(token) {
          return katex.renderToString(token.text, { displayMode: true, throwOnError: false });
        }
      },
      {
        name: 'displayMath',
        level: 'block',
        start(src) { return src.indexOf('\\['); },
        tokenizer(src) {
          if (!src.startsWith('\\[')) return;
          const end = src.indexOf('\\]');
          if (end < 2) return;
          return { type: 'displayMath', raw: src.slice(0, end + 2), text: src.slice(2, end) };
        },
        renderer(token) {
          return katex.renderToString(token.text, { displayMode: true, throwOnError: false });
        }
      },
      {
        name: 'inlineMath',
        level: 'inline',
        start(src) { return src.indexOf('\\('); },
        tokenizer(src) {
          if (!src.startsWith('\\(')) return;
          const end = src.indexOf('\\)');
          if (end < 2) return;
          return { type: 'inlineMath', raw: src.slice(0, end + 2), text: src.slice(2, end) };
        },
        renderer(token) {
          return katex.renderToString(token.text, { displayMode: false, throwOnError: false });
        }
      },
      {
        name: 'inlineDollarMath',
        level: 'inline',
        start(src) {
          let index = src.indexOf('$');
          while (index >= 0 && src[index - 1] === '\\') index = src.indexOf('$', index + 1);
          return index;
        },
        tokenizer(src) {
          if (!src.startsWith('$') || src.startsWith('$$')) return;
          let end = src.indexOf('$', 1);
          while (end >= 0 && src[end - 1] === '\\') end = src.indexOf('$', end + 1);
          if (end < 2) return;
          return { type: 'inlineDollarMath', raw: src.slice(0, end + 1), text: src.slice(1, end) };
        },
        renderer(token) {
          return katex.renderToString(token.text, { displayMode: false, throwOnError: false });
        }
      },
    ];
    marked.use({ extensions: mathExtensions });

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
    function createTocHeader() {
      const header = document.createElement('div');
      header.className = 'toc-header';
      const heading = document.createElement('h2');
      heading.textContent = '目录';
      const refresh = document.createElement('button');
      refresh.id = 'refresh-sessions';
      refresh.type = 'button';
      refresh.textContent = '刷新';
      header.append(heading, refresh);
      return header;
    }

    function clearTocStatus(text) {
      tocElement.replaceChildren(createTocHeader());
      const status = document.createElement('p');
      status.className = 'status';
      status.textContent = text;
      tocElement.append(status);
    }

    tocElement.addEventListener('click', event => {
      if (event.target.closest('#refresh-sessions')) loadIndex(true);
    });


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

    function renderUploadList() {
      uploadList.replaceChildren();
      if (!uploadItems.length) {
        const empty = document.createElement('p');
        empty.className = 'status';
        empty.textContent = '还没有图片。';
        uploadList.append(empty);
      }

      uploadItems.forEach(item => {
        const row = document.createElement('article');
        row.className = 'upload-item';
        const preview = document.createElement('img');
        preview.className = 'upload-preview';
        preview.src = item.previewUrl;
        preview.alt = item.name;
        const details = document.createElement('div');
        const name = document.createElement('strong');
        name.className = 'upload-name';
        name.textContent = item.name;
        const status = document.createElement('span');
        status.className = item.error ? 'status upload-name' : 'upload-name';
        status.textContent = item.error || item.status;
        details.append(name, status);
        if (item.path) {
          const path = document.createElement('code');
          path.className = 'upload-path';
          path.textContent = `@${item.path}`;
          details.append(path);
        }
        const remove = document.createElement('button');
        remove.className = 'upload-remove';
        remove.type = 'button';
        remove.textContent = '删除';
        remove.setAttribute('aria-label', `删除 ${item.name}`);
        remove.addEventListener('click', () => removeUploadItem(item.id));
        row.append(preview, details, remove);
        uploadList.append(row);
      });

      uploadCopyButton.disabled = !uploadItems.some(item => item.path);
    }
    function removeUploadItem(id) {
      const index = uploadItems.findIndex(item => item.id === id);
      if (index < 0) return;
      const [item] = uploadItems.splice(index, 1);
      URL.revokeObjectURL(item.previewUrl);
      renderUploadList();
    }

    async function uploadImage(item) {
      item.status = '上传中…';
      renderUploadList();
      try {
        const response = await fetch('/api/upload', {
          method: 'POST',
          headers: { 'Content-Type': item.file.type },
          body: item.file
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `上传失败：${response.status}`);
        item.path = data.path;
        item.status = '已上传';
      } catch (error) {
        item.error = error.message || '上传失败。';
      }
      renderUploadList();
    }

    function addUploadFiles(files) {
      [...files].filter(file => file && file.type.startsWith('image/')).forEach(file => {
        const item = {
          id: ++uploadSequence,
          file,
          name: file.name || `粘贴图片 ${uploadSequence}`,
          previewUrl: URL.createObjectURL(file),
          status: '等待上传',
          path: '',
          error: ''
        };
        uploadItems.push(item);
        void uploadImage(item);
      });
      renderUploadList();
    }

    function clearUploads() {
      uploadItems.forEach(item => URL.revokeObjectURL(item.previewUrl));
      uploadItems = [];
      renderUploadList();
    }

    uploadOpenButton.addEventListener('click', () => {
      uploadDialog.showModal();
      uploadDropzone.focus();
    });
    uploadCloseButton.addEventListener('click', () => uploadDialog.close());
    uploadClearButton.addEventListener('click', clearUploads);
    uploadInput.addEventListener('change', () => {
      addUploadFiles(uploadInput.files);
      uploadInput.value = '';
    });
    uploadDropzone.addEventListener('click', () => uploadInput.click());
    uploadDropzone.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        uploadInput.click();
      }
    });
    uploadDialog.addEventListener('paste', event => {
      const files = [...(event.clipboardData?.items || [])]
        .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
        .map(item => item.getAsFile())
        .filter(Boolean);
      if (files.length) {
        event.preventDefault();
        addUploadFiles(files);
      }
    });
    uploadCopyButton.addEventListener('click', () => {
      const paths = uploadItems.filter(item => item.path).map(item => `@${item.path}`).join(' ');
      if (paths) void copyMarkdown(uploadCopyButton, paths);
    });
    renderUploadList();
    function renderConversation(messages) {
      tocElement.replaceChildren(createTocHeader());
      messagesElement.replaceChildren();

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
        copy.textContent = '复制';
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
            const target = document.getElementById(id);
            const top = target.getBoundingClientRect().top - messagesElement.getBoundingClientRect().top + messagesElement.scrollTop;
            messagesElement.scrollTo({ top, behavior: 'auto' });
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

    async function loadSelectedSession(scrollToBottom = true) {
      const path = selectedPath();
      if (!path) {
        clearTocStatus('选择一个会话。');
        clearAndStatus(messagesElement, '选择一个会话。');
        return;
      }
      if (!knownSessions.has(path)) {
        clearTocStatus('会话不存在。');
        clearAndStatus(messagesElement, '会话不存在或已被删除。');
        return;
      }

      const version = ++requestVersion;
      clearTocStatus('加载中…');
      clearAndStatus(messagesElement, '加载中…');
      try {
        const data = await fetchJson(`/api/session?path=${encodeURIComponent(path)}`);
        if (version === requestVersion) {
          renderConversation(data.messages);
          if (scrollToBottom) messagesElement.scrollTo({ top: messagesElement.scrollHeight, behavior: 'auto' });
        }
      } catch (error) {
        if (version === requestVersion) {
          clearTocStatus('无法加载会话。');
          clearAndStatus(messagesElement, error.message);
        }
      }
    }

    async function loadIndex(scrollToBottom = true) {
      try {
        const groups = await fetchJson('/api/sessions');
        knownSessions = new Set();
        const header = sessionsElement.querySelector('.sidebar-header');
        sessionsElement.replaceChildren(header);

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
        await loadSelectedSession(scrollToBottom);
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
    def do_POST(self) -> None:
        request = urlparse(self.path)
        if request.path != "/api/upload":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "不存在的路径。"})
            return

        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length) if content_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self.send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "请求缺少 Content-Length。"})
            return
        if length > MAX_IMAGE_BYTES:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "单张图片不能超过 20 MB。"})
            return

        data = self.rfile.read(length)
        try:
            path = save_uploaded_image(self.headers.get("Content-Type", ""), data)
        except UploadError as error:
            self.send_json(error.status, {"error": str(error)})
            return
        self.send_json(HTTPStatus.CREATED, {"path": str(path)})


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
