# Chrome Safari Bookmarks Sync

Conservative bidirectional bookmark sync between Google Chrome and Safari on macOS.

macOS 上 Google Chrome 与 Safari 的保守型双向书签同步工具。

## English

### What It Does

- Syncs missing bookmarks in both directions between Google Chrome and Safari.
- Deduplicates by normalized URL.
- Adds Chrome-only bookmarks to Safari under `Imported from Google Chrome`.
- Adds Safari-only bookmarks to Chrome under `Other Bookmarks / Imported from Safari`.
- Never deletes, renames, or moves existing bookmarks.
- Creates timestamped backups before every write.
- Recomputes Chrome's bookmark checksum before writing.
- Runs locally and uses only the Python standard library.

### One Command

Run a full preflight, unit tests, and bidirectional sync:

```sh
./sync_bookmarks.sh
```

Preview changes without writing:

```sh
./sync_bookmarks.sh --dry-run
```

The one-command wrapper hides bookmark URLs by default. Use the Python entry point with `--preview-limit 20` if you want to inspect individual URLs.

Sync only one direction:

```sh
./sync_bookmarks.sh --mode chrome-to-safari
./sync_bookmarks.sh --mode safari-to-chrome
```

Install automatic sync after a successful run:

```sh
./sync_bookmarks.sh --install-agent
```

### Direct Python Usage

```sh
python3 chrome_to_safari.py --mode both
python3 chrome_to_safari.py --mode both --apply
python3 chrome_to_safari.py --mode both --preview-limit 0
```

Advanced examples:

```sh
python3 chrome_to_safari.py \
  --chrome-bookmarks "$HOME/Library/Application Support/Google/Chrome/Default/Bookmarks" \
  --safari-bookmarks "$HOME/Library/Safari/Bookmarks.plist" \
  --safari-target-folder "Imported from Google Chrome" \
  --chrome-target-folder "Imported from Safari" \
  --apply
```

### Automatic Sync

Install the LaunchAgent:

```sh
chmod +x sync_bookmarks.sh install_launch_agent.sh uninstall_launch_agent.sh
./install_launch_agent.sh
```

Uninstall it:

```sh
./uninstall_launch_agent.sh
```

The LaunchAgent runs at login and watches:

- `~/Library/Application Support/Google/Chrome/Default/Bookmarks`
- `~/Library/Safari/Bookmarks.plist`

### macOS Permissions

Safari bookmarks are protected by macOS privacy controls. If the tool cannot read or write `~/Library/Safari/Bookmarks.plist`, grant Full Disk Access to your terminal app or `/usr/bin/python3`.

Chrome may keep an in-memory bookmark model while running. The tool rewrites the JSON checksum correctly, but closing and reopening Chrome is the safest way to confirm Chrome has accepted filesystem-level changes.

### Safety Model

This tool is intentionally conservative. It only adds missing URLs. It does not try to make both browsers identical because that would require destructive operations and conflict resolution.

Backups are written next to the original files with names like:

```text
Bookmarks.bak-YYYYMMDD-HHMMSS
Bookmarks.plist.bak-YYYYMMDD-HHMMSS
```

### Development

```sh
python3 -m py_compile chrome_to_safari.py tests/test_chrome_to_safari.py
python3 -m unittest discover -s tests -v
```

## 中文

### 功能

- 在 Google Chrome 和 Safari 之间双向补齐缺失书签。
- 按规范化 URL 自动去重。
- Chrome 独有书签会写入 Safari 的 `Imported from Google Chrome` 文件夹。
- Safari 独有书签会写入 Chrome 的 `Other Bookmarks / Imported from Safari` 文件夹。
- 不删除、不重命名、不移动现有书签。
- 每次写入前都会创建带时间戳的备份。
- 写入 Chrome 前会重新计算书签文件的 checksum。
- 完全本地运行，只使用 Python 标准库。

### 一条命令完成

执行预检、单元测试和双向同步：

```sh
./sync_bookmarks.sh
```

只预览，不写入：

```sh
./sync_bookmarks.sh --dry-run
```

一键脚本默认不打印具体书签 URL，避免终端日志泄露私人书签。如果需要检查具体 URL，可直接使用 Python 入口并设置 `--preview-limit 20`。

只同步一个方向：

```sh
./sync_bookmarks.sh --mode chrome-to-safari
./sync_bookmarks.sh --mode safari-to-chrome
```

同步成功后安装自动同步：

```sh
./sync_bookmarks.sh --install-agent
```

### 直接使用 Python

```sh
python3 chrome_to_safari.py --mode both
python3 chrome_to_safari.py --mode both --apply
python3 chrome_to_safari.py --mode both --preview-limit 0
```

高级用法：

```sh
python3 chrome_to_safari.py \
  --chrome-bookmarks "$HOME/Library/Application Support/Google/Chrome/Default/Bookmarks" \
  --safari-bookmarks "$HOME/Library/Safari/Bookmarks.plist" \
  --safari-target-folder "Imported from Google Chrome" \
  --chrome-target-folder "Imported from Safari" \
  --apply
```

### 自动同步

安装 LaunchAgent：

```sh
chmod +x sync_bookmarks.sh install_launch_agent.sh uninstall_launch_agent.sh
./install_launch_agent.sh
```

卸载 LaunchAgent：

```sh
./uninstall_launch_agent.sh
```

LaunchAgent 会在登录时运行，并监听：

- `~/Library/Application Support/Google/Chrome/Default/Bookmarks`
- `~/Library/Safari/Bookmarks.plist`

### macOS 权限

Safari 书签受 macOS 隐私权限保护。如果工具无法读取或写入 `~/Library/Safari/Bookmarks.plist`，请给终端应用或 `/usr/bin/python3` 授予 Full Disk Access。

Chrome 运行时可能持有内存态书签模型。工具会正确重写 Chrome JSON checksum，但最稳妥的验证方式仍然是关闭并重新打开 Chrome。

### 安全模型

这个工具刻意保持保守。它只补齐缺失 URL，不试图把两个浏览器完全变成同一棵书签树，因为那需要破坏性操作和冲突处理。

备份文件会保存在原文件旁边，例如：

```text
Bookmarks.bak-YYYYMMDD-HHMMSS
Bookmarks.plist.bak-YYYYMMDD-HHMMSS
```

### 开发验证

```sh
python3 -m py_compile chrome_to_safari.py tests/test_chrome_to_safari.py
python3 -m unittest discover -s tests -v
```

## License

MIT
