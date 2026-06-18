# Chrome Safari Bookmarks Sync

Conservative bidirectional bookmark sync between Google Chrome and Safari on macOS.

macOS 上 Google Chrome 与 Safari 的保守型双向书签同步工具。

## English

### What It Does

- Syncs missing bookmarks in both directions between Google Chrome and Safari.
- Deduplicates with a conservative URL comparison that preserves `www`, query order, parameters, and fragments.
- Can optionally ignore known advertising parameters with `--dedup-policy tracking`.
- Skips `javascript:` and `data:` bookmarks unless `--include-active-bookmarks` is set.
- Adds Chrome-only bookmarks to Safari under `Imported from Google Chrome`.
- Adds Safari-only bookmarks to Chrome under `Other Bookmarks / Imported from Safari`.
- Never deletes, renames, or moves existing bookmarks.
- Creates timestamped backups before every write.
- Retains the 10 newest backups per browser file by default.
- Recomputes Chrome's bookmark checksum before writing.
- Detects concurrent file changes and overlapping sync processes before replacing data.
- Discovers every current Chrome profile and keeps profile folders separate when more than one exists.
- Runs locally and uses only the Python standard library.

### One Command

Run a preflight and bidirectional sync:

```sh
./sync_bookmarks.sh
```

Close each destination browser before writing. The command safely aborts when Chrome or Safari is running and has pending inbound bookmarks.

Run unit tests before syncing when desired:

```sh
./sync_bookmarks.sh --run-tests
```

Preview changes without writing:

```sh
./sync_bookmarks.sh --dry-run
```

All entry points hide bookmark URLs by default. Use `--preview-limit 20` if you want to inspect individual URLs.

Sync only one direction:

```sh
./sync_bookmarks.sh --mode chrome-to-safari
./sync_bookmarks.sh --mode safari-to-chrome
```

Use tracking-parameter deduplication only when that tradeoff is intentional:

```sh
./sync_bookmarks.sh --dedup-policy tracking
```

Bypass the running-browser protection:

```sh
./sync_bookmarks.sh --allow-running-browsers
```

This override can lose changes when the browser later flushes its in-memory bookmark model.

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

Install the console command locally:

```sh
python3 -m pip install .
chrome-safari-bookmarks-sync --mode both
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

The LaunchAgent runs at login, checks every five minutes, and watches:

- every Chrome profile `Bookmarks` file present during installation
- `~/Library/Safari/Bookmarks.plist`

It runs quietly, does not write bookmark URLs to logs, and keeps the destination-browser safety check enabled.

### macOS Permissions

Safari bookmarks are protected by macOS privacy controls. If the tool cannot read or write `~/Library/Safari/Bookmarks.plist`, grant Full Disk Access to your terminal app or `/usr/bin/python3`.

Chrome and Safari can keep in-memory bookmark models while running. The tool therefore refuses to write to a destination browser that is open. `--allow-running-browsers` is available for users who explicitly accept that risk.

### Safety Model

This tool is intentionally conservative. It only adds missing URLs. It does not try to make both browsers identical because that would require destructive operations and conflict resolution.

Writes use a process lock, source fingerprints, temporary files, `fsync`, and atomic replacement. If a source file changes after planning, the run stops before replacing it.

Backups are written next to the original files with names like:

```text
Bookmarks.bak-YYYYMMDD-HHMMSS-microseconds
Bookmarks.plist.bak-YYYYMMDD-HHMMSS-microseconds
```

The newest 10 backups are retained by default. Change this with `--backup-retention`.

### Known Limits

- Synchronization is additive. It does not propagate deletions, renames, or moves.
- Safari imports go to the first selected Chrome bookmark file, normally the `Default` profile.
- A two-file update cannot be atomic across both browser files. If the second write is interrupted, rerunning converges the additive state.
- The LaunchAgent discovers new profiles during periodic runs, but rerunning the installer is required to add immediate file watching for a newly created profile.

### Development

```sh
python3 -m py_compile chrome_to_safari.py tests/test_chrome_to_safari.py
python3 -m unittest discover -s tests -v
```

## 中文

### 功能

- 在 Google Chrome 和 Safari 之间双向补齐缺失书签。
- 默认使用保守型 URL 判等，保留 `www`、查询参数顺序、参数内容和片段。
- 可通过 `--dedup-policy tracking` 显式忽略已知广告跟踪参数。
- 默认跳过 `javascript:` 和 `data:` 书签，只有传入 `--include-active-bookmarks` 才会同步。
- Chrome 独有书签会写入 Safari 的 `Imported from Google Chrome` 文件夹。
- Safari 独有书签会写入 Chrome 的 `Other Bookmarks / Imported from Safari` 文件夹。
- 不删除、不重命名、不移动现有书签。
- 每次写入前都会创建带时间戳的备份。
- 默认每个浏览器文件保留最新 10 份备份。
- 写入 Chrome 前会重新计算书签文件的 checksum。
- 写入前检测并发文件变化和重复运行的同步进程。
- 自动发现当前所有 Chrome Profile，多 Profile 时会保留 Profile 文件夹层级。
- 完全本地运行，只使用 Python 标准库。

### 一条命令完成

执行预检和双向同步：

```sh
./sync_bookmarks.sh
```

写入前请关闭作为目标端的浏览器。Chrome 或 Safari 正在运行且存在待写入书签时，命令会安全中止。

需要时可先执行单元测试再同步：

```sh
./sync_bookmarks.sh --run-tests
```

只预览，不写入：

```sh
./sync_bookmarks.sh --dry-run
```

所有入口默认不打印具体书签 URL，避免终端日志泄露私人书签。需要检查具体 URL 时可设置 `--preview-limit 20`。

只同步一个方向：

```sh
./sync_bookmarks.sh --mode chrome-to-safari
./sync_bookmarks.sh --mode safari-to-chrome
```

只有明确接受该取舍时才启用跟踪参数去重：

```sh
./sync_bookmarks.sh --dedup-policy tracking
```

绕过浏览器运行状态保护：

```sh
./sync_bookmarks.sh --allow-running-browsers
```

浏览器之后刷新内存态书签时，这个选项可能导致刚写入的内容丢失。

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

安装本地命令行入口：

```sh
python3 -m pip install .
chrome-safari-bookmarks-sync --mode both
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

LaunchAgent 会在登录时运行、每五分钟检查一次，并监听：

- 安装时已存在的每个 Chrome Profile `Bookmarks` 文件
- `~/Library/Safari/Bookmarks.plist`

后台任务静默运行，不会把书签 URL 写入日志，并保留目标浏览器关闭检查。

### macOS 权限

Safari 书签受 macOS 隐私权限保护。如果工具无法读取或写入 `~/Library/Safari/Bookmarks.plist`，请给终端应用或 `/usr/bin/python3` 授予 Full Disk Access。

Chrome 和 Safari 运行时可能持有内存态书签模型。因此，目标浏览器处于打开状态时，工具默认拒绝写入。明确接受风险时可使用 `--allow-running-browsers`。

### 安全模型

这个工具刻意保持保守。它只补齐缺失 URL，不试图把两个浏览器完全变成同一棵书签树，因为那需要破坏性操作和冲突处理。

写入流程包含进程锁、源文件指纹、临时文件、`fsync` 和原子替换。规划完成后如果源文件发生变化，本次运行会在替换前中止。

备份文件会保存在原文件旁边，例如：

```text
Bookmarks.bak-YYYYMMDD-HHMMSS-microseconds
Bookmarks.plist.bak-YYYYMMDD-HHMMSS-microseconds
```

默认保留最新 10 份备份，可通过 `--backup-retention` 调整。

### 已知边界

- 同步采用只增不减策略，不传播删除、重命名或移动操作。
- Safari 书签写入第一个选中的 Chrome 文件，通常是 `Default` Profile。
- 两个浏览器文件无法组成跨文件原子事务。第二次写入被中断时，重新运行即可收敛只增状态。
- 后台任务会在周期检查时发现新 Profile，但新建 Profile 后需要重新运行安装脚本，才能立即监听它的文件变化。

### 开发验证

```sh
python3 -m py_compile chrome_to_safari.py tests/test_chrome_to_safari.py
python3 -m unittest discover -s tests -v
```

## License

MIT
