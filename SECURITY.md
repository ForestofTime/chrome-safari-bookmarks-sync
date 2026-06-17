# Security Policy

## English

This project reads and writes local browser bookmark files. Bookmark files can contain private URLs, folder names, and browsing history hints.

Do not open issues or pull requests that include real `Bookmarks`, `Bookmarks.plist`, backup files, logs, or screenshots containing private URLs. Use synthetic fixtures instead.

The sync tool creates timestamped backups before writing. Review the dry-run output before applying changes.

## 中文

本项目会读取和写入本机浏览器书签文件。书签文件可能包含私人 URL、文件夹名称以及浏览习惯线索。

请不要在 issue 或 pull request 中提交真实的 `Bookmarks`、`Bookmarks.plist`、备份文件、日志，或包含私人 URL 的截图。测试请使用合成数据。

工具在写入前会创建带时间戳的备份。建议先查看 dry-run 输出，再执行写入。
