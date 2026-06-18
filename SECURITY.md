# Security Policy

## English

This project reads and writes local browser bookmark files. Bookmark files can contain private URLs, folder names, and browsing history hints.

Do not open issues or pull requests that include real `Bookmarks`, `Bookmarks.plist`, backup files, logs, or screenshots containing private URLs. Use synthetic fixtures instead.

The sync tool uses process locking, source-file fingerprints, atomic replacement, and bounded timestamped backups. It refuses to write to a running destination browser unless the user explicitly passes `--allow-running-browsers`.

The LaunchAgent uses private log files, hides bookmark URLs, and runs with the same local-user permissions as the installer.

URL previews and active `javascript:` or `data:` bookmarks are disabled by default. Enabling either behavior is an explicit user choice.

## 中文

本项目会读取和写入本机浏览器书签文件。书签文件可能包含私人 URL、文件夹名称以及浏览习惯线索。

请不要在 issue 或 pull request 中提交真实的 `Bookmarks`、`Bookmarks.plist`、备份文件、日志，或包含私人 URL 的截图。测试请使用合成数据。

工具使用进程锁、源文件指纹、原子替换和有数量上限的时间戳备份。目标浏览器正在运行时会拒绝写入，除非用户显式传入 `--allow-running-browsers`。

LaunchAgent 使用仅当前用户可读的日志文件，不记录书签 URL，并以安装者的本地用户权限运行。

URL 预览以及 `javascript:`、`data:` 主动书签同步默认关闭，启用这些行为需要用户显式选择。
