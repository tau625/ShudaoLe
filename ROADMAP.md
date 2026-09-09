# 书到了 · 改进路线图（Roadmap）

[简体中文](ROADMAP.md) | [English](ROADMAP_EN.md)

本路线图基于 v1.2.0 代码全面审查（2026-09）制定，按优先级从高到低排列。每项附理由与预期效果，用复选框跟踪进度。方向已确认：**全面工程化** + 四个功能方向（下载体验、目录扩展、脚本化、更新推送）。

> 当前健康面（无需重复建设）：本地服务仅绑定 127.0.0.1、请求来源校验（防 DNS 重绑定）、路径穿越防护、HTML 转义、SSRF URL 校验、分类异常体系、请求重试——这些已经做得不错。

---

## P0 · 正确性修复（立即执行，零架构风险）

可在一次补丁版本（v1.2.1）内全部完成，不动架构。

- [ ] **P0-1 修浏览器 profile 目录命名泄漏**
  `auto_fetch_token.py:279-281` 把独立浏览器 profile 硬编码到 `~/.workbuddy/smartedu-token-profile` —— `.workbuddy` 是开发机 AI 工具目录名，会在**最终用户机器**上创建语义不明的隐藏目录。
  改为 `~/.config/shudaole/token-profile`（Windows 下 `%USERPROFILE%\.config\shudaole\` 即可，无需写注册表）。
  *理由*：品牌一致 + 避免在用户机器留下无法解释的目录。*效果*：新用户机器目录干净、可预期。
  *迁移*：启动时若发现旧路径存在，静默搬移或直接弃用（登录态重登一次即可）。

- [ ] **P0-2 目录缓存原子写**
  `smartedu_downloader.py:811-820` 直接 `write_text`，进程中断会留下半截 JSON；且 `except OSError: pass` 静默吞错。
  改为「写 `.tmp` → `os.replace()`」原子替换，失败记入日志。
  *理由*：缓存损坏虽只触发重下，但半截文件 + 无声失败是坏习惯，一行成本修掉。*效果*：缓存永不半截，失败可见。

- [ ] **P0-3 WebSocket 连续帧支持**
  `auto_fetch_token.py` 的 `_WS.recv` 不处理 `OP_CONTINUATION`（opcode 0x0）分片帧，CDP 长消息（如大体积 Network 事件）可能被截断，导致令牌偶发漏抓。
  *理由*：这是协议正确性问题，不是优化。*效果*：令牌抓取在复杂页面下稳定。

- [ ] **P0-4 令牌落盘位置与权限**
  `token.txt` 当前写在脚本/exe 同目录，明文。改为优先存用户配置目录（`~/.config/shudaole/token.txt`），POSIX 下 `chmod 600`；同目录旧文件仍兼容读取。
  *理由*：脚本目录可能是共享/同步盘，明文凭据不该随项目目录走。*效果*：凭据泄漏面收窄。

- [ ] **P0-5 收窄裸 `except`**
  `auto_fetch_token.py` 多处 `except Exception: pass` 吞掉异常上下文。改为捕获具体异常并记日志（至少 stderr）。
  *理由*：无声失败最耗排查时间。*效果*：故障可诊断。

---

## P1 · 工程化地基（所有功能的前置）

目标：把「三个大单文件 + 零测试 + 零门禁」变成可持续演进的工程结构。**维持 Python 3.8 基线**（README 已承诺；统一 `from __future__ import annotations`）。

- [ ] **P1-1 pyproject.toml + 包结构拆分**
  新建包 `shudaole/`：`catalog.py`（目录抓取/规范化/级联筛选/常量）、`download.py`（下载核心）、`net.py`（Session/重试/URL 校验）、`cli.py`、`config.py`（配置加载）、`token.py`（现 auto_fetch_token 迁入）、`gui/`（服务 + 静态页拆出）。
  旧入口 `smartedu_downloader.py` / `smartedu_downloader_gui.py` 保留为 **thin shim**（转发到包入口）——`python smartedu_downloader.py` 用法与全部文档零改动。
  *理由*：1806 行单文件的常量分散在 L1164-1211、`normalize_catalog` 127 行、`process_one` 内三段重复下载逻辑，继续堆功能只会更糟。*效果*：模块边界清晰，后续每一项改造的作用域可控。

- [ ] **P1-2 build.spec / CI 适配拆包**
  `hiddenimports` 调整为包内模块；datas 追加包内数据文件；release.yml 加 `cache: pip`；新增「拆包后冒烟构建」job（tag 前保证 PyInstaller 收集完整）。
  *理由*：拆包最大风险就是 PyInstaller 漏收集，必须在 CI 层兜住。*效果*：发版永远不会再出「本地好好的、打包后缺模块」。

- [ ] **P1-3 日志系统（logging 迁移）**
  核心统一 `logging.getLogger("shudaole")`；GUI 侧保留现有 `on_log` 回调门面——加一个自定义 Handler 把日志写进界面日志缓冲，实现无感迁移。
  *理由*：print/回调散落各处，级别、时间戳、去重都做不了。*效果*：可按级别过滤，GUI 日志与 CLI 日志同源。

- [ ] **P1-4 pytest 测试地基**
  首轮覆盖纯函数：`normalize_title` / `build_filename` / `parse_content_id` / `normalize_catalog` / 级联筛选 / `validate_public_http_url`（目标 ≥90% 分支覆盖）；网络层用 `requests-mock` 模拟目录/详情/PDF（含 401、Range 206、重试路径）。整体目标：首轮 60%，稳定后 75-80%。
  *理由*：命名/筛选逻辑是本项目最复杂、最易回归的部分，也是最值得测的部分。*效果*：P2 动下载内核时敢下手。

- [ ] **P1-5 CI 质量门禁（ci.yml）**
  新开 workflow：push/PR 触发，矩阵 Python 3.8/3.10/3.12，`setup-python` 带 `cache: pip`，步骤 ruff（lint+format 检查）→ mypy（宽松起步，逐步收紧）→ pytest --cov。
  *理由*：门禁放在日常流而不是发版流，问题在提交时就拦住。*效果*：代码质量可持续，三人协作也不怕。

---

## P2 · 功能增强（按依赖顺序执行）

### Batch 2：下载内核

- [ ] **P2-1 并发下载（worker 池，并发 3-5）**
  放在 downloader 核心层实现（`download_many`），GUI 的 `run_task` 只做编排；并发度默认 3，可调上限 5——对教材平台友好，避免触发限流。
  cancel 从 bool 标志升级为 `threading.Event`：worker 在文件间隙检查，进行中的文件优雅收尾（关闭流、保留 .part）。
  *理由*：当前单线程顺序下载，批量下整套教材时速度是最大痛点。*效果*：批量场景 3-5 倍提速（受平台带宽与限流约束）。

- [ ] **P2-2 断点续传 + 任务持久化**
  下载先写 `xxx.pdf.part`，完成才改名；重启时以 `.part` 大小为起点发 `Range` 请求续传（服务端不支持 206 则整体重下）。
  任务列表持久化到 `~/.config/shudaole/tasks.json`（含链接、目标目录、状态、字节数、时间戳）；GUI 启动时检测未完成任务，提示「继续 / 放弃」。
  *理由*：当前任务状态纯内存，关掉界面全丢；大 PDF 下到 90% 断网只能从头来。*效果*：下载可靠性质变，界面可关可重启。

- [ ] **P2-3 目录扩展（出版社配置外置）**
  `PUB_GROUPS` / `SUPPORTED_SCOPE` 外置：包内 `data/pubs.json` 为默认值，用户级 `~/.config/shudaole/pubs.json` 深合并覆盖/追加；Web UI 加「管理出版社」面板（增删组与标签，写用户文件）。
  *理由*：现在加北师大版要改源码重启，普通用户做不到。*效果*：不改代码即可开放任意版本/学段。

- [ ] **P2-4 脚本化能力**
  - 导出书单：筛选结果导出 CSV / Markdown（含全部维度字段，供打印或共享）
  - CLI 批处理：`--list-file` 语义增强 + `--dry-run` + 退出码规范化（供外部脚本调用）
  - 定时同步：`--watch` 模式（轮询目录更新，新教材自动入队，可配间隔）
  *理由*：教师/教研组等群体用户需要批量、无人值守场景。*效果*：从「手动工具」升级为「可编排的工具」。

### Batch 3：集成与更新

- [ ] **P2-5 应用内更新检查**
  启动后异步查 `https://api.github.com/repos/tau625/ShudaoLe/releases/latest`（仅取版本号，无任何遥测/PII，走系统代理），新版本时界面顶部横幅提示 + Release 页链接。
  **不做自动下载/自动更新**：无签名 exe 自动替换是杀软误报与法律风险的双重雷区。默认手动「检查更新」按钮，可在设置中关闭网络检查。
  *理由*：用户散落各地，没有更新通道就没有安全修复触达。*效果*：版本碎片收敛，P0 级修复能推到用户。

---

## P3 · 锦上添花

- [ ] **P3-1 前端轮询合并**
  现有 4 个独立定时器（700ms 状态 / 1000ms 令牌 / 3000ms 残留 / 1500ms 计数）合并为统一刷新协调器，按页面可见性与状态机调节频率；令牌轮询三处重复代码合一。
  *理由*：降低常驻开销与代码重复。*效果*：前端行为可预测、好调试。

- [ ] **P3-2 GUI 路由表化**
  `do_GET`/`do_POST` 的 if-else 硬编码路由改为字典分发；`pick_folder` 三份跨平台重复合并为一个带平台分支的函数。
  *理由*：端点已 10+，继续 if-else 不可维护。*效果*：加端点变成加一行表项。

- [ ] **P3-3 文档与开发笔记同步**
  拆包后同步更新 README 目录结构与「开发笔记」章节；CHANGELOG.md 建立（目前只有 Release notes）。
  *理由*：文档与代码不一致是维护成本的最大来源。*效果*：新人（或未来的自己）一小时能上手。

---

## 执行批次与依赖

| 批次 | 内容 | 前置 |
|---|---|---|
| Batch 0 | P0-1 ~ P0-5（发 v1.2.1 补丁） | 无 |
| Batch 1 | P1 工程化地基（拆包/日志/测试/CI） | 建议在 P0 发版后 |
| Batch 2 | P2-1/2/3/4（下载内核 + 配置外置 + 脚本化） | P1 完成 |
| Batch 3 | P2-5 更新检查 | P1 完成（可与 Batch 2 部分并行） |
| Batch 4 | P3 全部 | 无硬依赖，穿插进行 |

## 风险清单

- **拆包 vs PyInstaller 收集**：动态 import 漏收集 → thin shim + 显式 hiddenimports + CI 冒烟构建三重保险。
- **Python 3.8 基线**：维持承诺不抬升（`from __future__ import annotations` 统一注解写法）；仅当必需依赖放弃 3.8 时再议，需在 Release notes 显著说明。
- **更新检查合规**：本工具法律敏感（PolyForm Noncommercial + 教材版权）。仅访问公开 GitHub API、不传任何用户数据、默认手动、可彻底关闭；绝不捆绑自动更新器。
- **并发下载与平台限流**：并发度上限 5 并在文档说明「请适度使用」；如遇平台侧 429，自动降并发并提示。
