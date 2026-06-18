# Project Audit

Audit date: 2026-06-18

## English

### Scope

The review covered bookmark comparison, bidirectional planning, Chrome checksum generation, Safari plist generation, concurrent writes, atomicity, backup behavior, profile discovery, LaunchAgent privacy, performance, tests, and documentation.

### Remediated Findings

| Area | Finding | Resolution |
| --- | --- | --- |
| Accuracy | URL comparison removed `www`, fragments, generic parameters, and query ordering, which could merge distinct bookmarks. | Conservative comparison is now the default. Tracking-parameter removal is explicit. |
| Integrity | Browser or another sync process could modify a file between planning and replacement. | Added a process lock, stable reads, SHA-256 fingerprints, and pre-replacement checks. |
| Durability | Temporary writes were not explicitly flushed to disk and replaced files could lose their original mode. | Added file and directory `fsync`, generated-format validation, and mode preservation. |
| Recovery | Backup names could collide within one second and backups grew without a limit. | Added microsecond names and a configurable retention limit of 10. |
| Browser state | A running destination browser could overwrite direct filesystem changes from its in-memory model. | Writes now require the destination browser to be closed unless explicitly overridden. |
| Privacy | The LaunchAgent used the Python default preview and could log private URLs. | Background runs are quiet, URL previews are disabled, and logs are mode `0600`. |
| Active content | `javascript:` and `data:` bookmarks were automatically propagated across browsers. | Active bookmarks are skipped by default and require explicit opt-in. |
| Coverage | Chrome discovery stopped at `Profile 4`. | All current non-symlink profile bookmark files are discovered. |
| Performance | Repeated folder lookup could become quadratic with many sibling folders. | Batch folder indexes make insertion effectively linear. |
| Startup speed | The one-command wrapper ran the full test suite before every routine sync. | Tests are now explicit with `--run-tests` and remain mandatory in CI. |
| Operations | The installer used deprecated `launchctl load/unload` and interpolated XML manually. | It now generates plist data with `plistlib` and uses `bootstrap/bootout`. |
| CI supply chain | GitHub Actions used mutable major-version tags and inherited default token permissions. | Actions are pinned to full commit SHAs and workflow permissions are read-only. |
| Packaging | `pyproject.toml` lacked a build backend and installed as `UNKNOWN` with the system Python toolchain. | Added an explicit setuptools backend, module declaration, and CI entry-point smoke test. |

### Verification

- 22 unit and integration tests pass on the local macOS Python 3.9.6 runtime.
- The macOS CI matrix covers Python 3.9 and 3.13.
- Real-data dry runs completed in about 0.10 to 0.31 seconds for a 235 KB Chrome file and a 1.3 MB Safari file.
- A synthetic 20,000-bookmark batch built each browser tree in under 0.20 seconds on the audit machine.
- A real apply with Chrome closed added 12 Safari-only bookmarks to Chrome, created a backup, and was followed by a zero-change bidirectional dry run.
- Destination-browser blocking and exact executable-path detection are covered by focused tests.

### Residual Risks

- Cross-file replacement cannot be one atomic filesystem operation. The additive design allows a rerun to converge after interruption.
- Browser files are private implementation formats and can change in future browser releases.
- Automatic sync depends on macOS Full Disk Access and local browser behavior.
- Deletions, renames, moves, and title conflicts are intentionally outside the current additive model.

## 中文

### 范围

本次审查覆盖 URL 判等、双向规划、Chrome checksum、Safari plist、并发写入、原子性、备份、Profile 发现、LaunchAgent 隐私、性能、测试和文档。

### 已修复问题

| 领域 | 问题 | 修复 |
| --- | --- | --- |
| 准确率 | 原规则会删除 `www`、片段、通用参数并重排查询参数，可能合并不同书签。 | 默认改为保守判等，跟踪参数过滤需要显式开启。 |
| 完整性 | 浏览器或另一个同步进程可能在规划后修改文件。 | 增加进程锁、稳定读取、SHA-256 指纹和替换前校验。 |
| 持久性 | 临时写入未显式刷盘，替换后可能改变原文件权限。 | 增加文件与目录 `fsync`、生成格式校验和权限保持。 |
| 恢复 | 同一秒内备份可能重名，备份数量没有上限。 | 使用微秒级文件名，默认最多保留 10 份。 |
| 浏览器状态 | 目标浏览器运行时，内存态可能覆盖磁盘写入。 | 默认要求目标浏览器关闭，允许用户显式绕过。 |
| 隐私 | 后台任务可能按 Python 默认值记录私人 URL。 | 后台静默运行、关闭 URL 预览，日志权限为 `0600`。 |
| 主动内容 | `javascript:` 和 `data:` 书签会自动跨浏览器传播。 | 默认跳过主动书签，只有显式选择时才同步。 |
| 覆盖 | Chrome 发现逻辑只检查到 `Profile 4`。 | 发现所有当前存在且不是符号链接的 Profile 书签文件。 |
| 性能 | 大量同级文件夹会导致重复线性查找。 | 批量文件夹索引将插入过程降为近似线性。 |
| 启动速度 | 一键脚本在每次常规同步前都执行完整测试。 | 测试改为通过 `--run-tests` 显式执行，CI 仍强制运行。 |
| 运维 | 安装脚本使用旧版 `launchctl` 接口并手工拼接 XML。 | 改用 `plistlib` 生成配置，并使用 `bootstrap/bootout`。 |
| CI 供应链 | GitHub Actions 使用可变主版本标签，并继承默认令牌权限。 | Action 固定到完整 commit SHA，工作流权限限制为只读。 |
| 打包 | `pyproject.toml` 缺少构建后端，在系统 Python 工具链中会被识别为 `UNKNOWN`。 | 增加明确的 setuptools 后端、模块声明和 CI 命令入口冒烟测试。 |

### 验证

- 本机 macOS Python 3.9.6 下 22 项单元与集成测试全部通过。
- macOS CI 矩阵覆盖 Python 3.9 和 3.13。
- 真实数据只读演练处理 235 KB Chrome 文件和 1.3 MB Safari 文件约需 `0.10s` 到 `0.31s`。
- 合成 20000 条书签时，两侧树构建耗时均低于 `0.20s`。
- Chrome 关闭时执行真实 apply，将 12 条 Safari 独有书签写入 Chrome、生成备份，随后双向 dry-run 为零变更。
- 目标浏览器拦截与精确可执行文件路径检测均有针对性测试覆盖。

### 残余风险

- 两个文件无法通过一次文件系统操作形成原子事务。只增策略允许中断后重新运行并收敛。
- 浏览器书签文件属于内部格式，未来浏览器版本可能改变结构。
- 自动同步依赖 macOS Full Disk Access 和浏览器本地行为。
- 删除、重命名、移动和标题冲突仍在当前只增模型范围之外。
