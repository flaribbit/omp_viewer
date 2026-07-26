# OMP Viewer

Oh My Pi 对话历史查看器。使用 Python 标准库提供本地网页，用于浏览 `~/.omp/agent/sessions` 中的会话记录。

## 运行

需要 Python 3.13 或更高版本：

```bash
python main.py
```

默认监听：

```text
http://localhost:1416
```

常用参数：

```bash
python main.py --host 127.0.0.1 --port 1416
python main.py --sessions-dir /path/to/sessions
```

## 上传图片

点击左侧的“上传图片”打开对话框，支持：

- 选择一张或多张图片
- 在对话框中按 `Ctrl+V` 粘贴图片
- 一次粘贴多张图片
- 一键复制所有已上传图片的 CLI 路径

复制结果示例：

```text
@/tmp/omp/00.jpg @/tmp/omp/01.png
```

图片保存在当前系统的临时目录下：

- Linux/macOS：`/tmp/omp`
- Windows：`%TEMP%\\omp`

上传文件名从 `00` 计数到 `99`，之后回到 `00`。重复编号会覆盖之前的文件。支持 PNG、JPEG、GIF、WebP 和 BMP，单张图片最大 20 MB。
对话框中的“删除”只会移除当前列表项和复制路径，不会删除磁盘上的图片文件。临时文件的清理由系统或用户环境负责。

## 依赖

后端只使用 Python 标准库，不需要安装额外的 Python 依赖。
