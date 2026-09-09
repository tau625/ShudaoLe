# 书到了 · 教材下载

[简体中文](README.md) | [English](README_EN.md)

> **本仓库不包含任何教材文件，仅提供下载工具。**

> **ShudaoLe (Book Arrived)** — one-click official textbook downloader for China's Smart Education platform.

从「国家中小学智慧教育平台」（https://basic.smartedu.cn ）下载教材 PDF 的命令行 + 网页界面工具。

## 功能演示

https://github.com/user-attachments/assets/2e59032c-0cc7-4e00-802e-52b620c795b5

> 演示内容：获取令牌 → 级联筛选 → 批量下载 → 打开文件（1 分 56 秒，1.5 倍速）。
> 更高画质见 [v1.2.0 Release 附件](https://github.com/tau625/ShudaoLe/releases/download/v1.2.0/shudaole-demo-1.5x.mp4)。

## 下载与安装

| 平台 | 方式 |
|---|---|
| Windows | 从 [Releases](https://github.com/tau625/ShudaoLe/releases/latest) 下载 `ShudaoLe-vX.Y.Z-windows-x64.zip`，解压后双击 `书到了.exe` |
| macOS（Apple Silicon） | 下载 `ShudaoLe-vX.Y.Z-macos.zip`，解压后在终端执行 `xattr -cr 书到了`（去除隔离属性）再 `./书到了` |
| Linux（x64，glibc ≥ 2.35） | 下载 `ShudaoLe-vX.Y.Z-linux-x64.tar.gz`，解压后 `chmod +x 书到了 && ./书到了` |
| 任意平台（源码运行） | 克隆本仓库 → `pip install -r requirements.txt` → `python smartedu_downloader_gui.py`（网页界面）或 `python smartedu_downloader.py`（命令行） |

> 非 Windows 平台说明：「一键获取令牌」已支持三平台（自动探测系统 Edge/Chrome）；
> 「浏览…」选目录在 Linux 需安装 zenity，macOS 原生支持，也可直接手动输入路径。
> macOS 打包未做签名，首次运行需绕过 Gatekeeper；Linux 打包版在 Ubuntu 22.04 构建。

## 目录结构

```
smartedu-教材下载器/
├── smartedu_downloader.py        # 命令行核心（解析/下载/重试/目录检索）
├── smartedu_downloader_gui.py    # 网页界面服务（本地 127.0.0.1，自动开浏览器）
├── smartedu_webui.html           # 网页界面（被 gui 读取，必须与脚本同目录）
├── auto_fetch_token.py           # 一键获取登录令牌（纯 Python，进程内整合，零依赖）
├── make_icon.py                  # 生成 app.ico 图标（纯标准库，无需 Pillow）
├── app.ico                       # 程序图标（由 make_icon.py 生成）
├── version_info.txt              # Windows 版本资源（打包时写入 exe 文件属性）
├── build.spec                    # PyInstaller 打包配置（onedir 模式）
├── build.bat                     # 一键打包入口（双击即可重新出 exe + zip）
├── build.py                      # 打包逻辑本体（清理/图标/PyInstaller/打 zip，三平台可运行）
├── requirements.txt              # Python 依赖
├── links.example.txt             # 批量链接输入示例
└── README.md                     # 本文件
```

> ⚠️ 直接源码运行时，`smartedu_downloader_gui.py` / `smartedu_webui.html` /
> `auto_fetch_token.py` 需放在**同一目录**（GUI 以自身所在目录为基准定位其他文件）。
> 打包成 exe 后则无此要求。

## 环境要求

- **Python 3.8+**（用 `requests`；进度条可选 `tqdm`）
- **无需 Node.js**：「一键获取令牌」已改为纯 Python 实现（通过 Chrome DevTools
  Protocol 驱动系统 Edge/Chrome），不再依赖 Node.js / playwright-core。

安装依赖：

```bash
pip install -r requirements.txt
```

> **关于「一键获取令牌」**：`auto_fetch_token.py` 用纯 Python 标准库（手写 WebSocket
> 连接 CDP 调试端口）驱动系统 Edge/Chrome 打开教材详情页，自动捕获 `x-nd-auth`。
> 它只依赖系统已装的 Edge/Chrome，**不需要**下载 playwright 浏览器二进制，也无需
> 联网拉取浏览器，更不需要 Node.js。

## 打包成 exe（推荐分发方式）

把整套工具打成 **Windows 可执行程序**，拷给别人**无需装 Python、无需装 Node.js**，解压即用：

```bash
pip install pyinstaller          # 首次需要
python make_icon.py              # 首次需要：生成 app.ico 图标
python -m PyInstaller build.spec  # 或双击 build.bat（含打 zip）
```

产物：
- `dist/书到了/` —— 可执行文件夹（exe + 运行时依赖）
- `dist/书到了.zip` —— 分发压缩包（build.bat 自动生成）

> **为什么是文件夹（onedir）而不是单文件（onefile）**：单文件 exe 每次启动都要把自身
> 解压到临时目录再运行，这种行为特征极易被 Windows Defender、360 等杀软误报为病毒。
> 改为 onedir 后启动不再自解压，误报率大幅下降，启动也更快。分发时打成 zip 同样方便。

> **零依赖**：Python 运行时已内置在 exe 内，「一键获取令牌」逻辑也已整合进 exe 进程内
> （不再 subprocess 调外部 Python）。对方机器只需满足 **Win10/11 自带 Edge 浏览器**
> 这一条，双击 exe 即可完整使用，包括一键获取令牌。

## 一、网页界面（推荐，功能最全）

```bash
python smartedu_downloader_gui.py                # 默认端口 8765，自动打开浏览器
python smartedu_downloader_gui.py --port 9000    # 指定端口
python smartedu_downloader_gui.py --no-browser   # 不自动打开浏览器，手动访问 http://127.0.0.1:8765
```

界面只监听本机 `127.0.0.1`，不对外网开放。

> **同一时间只允许运行一个实例**：启动时若检测到已有「书到了」在运行，会直接打开
> 现有窗口并提示"书到了已在运行中"，而不会启动第二个进程。这是为了防止多个进程
> 共用同一端口导致请求被随机分发（表现为"点了下载没反应、进度不动"）。
> 若 8765 被**其它程序**占用，程序会自动换一个空闲端口并在打开的页面地址中体现。

功能包括：

- 按 **学段 / 年级 / 科目 / 版本 / 册次 + 关键词** 浏览平台教材目录（下拉顺序即检索路径：先定几年级、什么科目，再挑版本与册次）
- **级联联动**：每个维度的候选值都按"排除自身选择"的条件实时计算，选中某值后仍可自由换到同维度的其它选项
- **已选条件标签**：当前筛选以标签展示，可单个移除或一键清空
- **空结果引导**：无匹配时直接给出"去掉哪个条件能找回多少条"，可一键放宽
- 结果按 **学段 → 年级 → 科目 → 版本 → 册次** 排序，同年级同科目的教材自然聚在一起
- 当前启用版本：**人教系列（人教版 / 统编版 / 人教鄂教版）与沪教版**，其余已隐藏；版本下拉提供「整套选 + 精确选」两级入口（扩展方法见文末）
- 勾选多本教材加入下载列表，实时查看进度
- 「打开文件 / 打开所在目录」定位下载结果
- 「一键自动获取令牌」按钮（自动打开浏览器，扫码/手机号登录后自动捕获令牌，无需 Node.js）

### 获取登录令牌（下载 PDF 必需）

平台对 PDF 下载启用登录鉴权，匿名请求一般返回 **401**。三种方式任选：

1. **网页界面内「一键自动获取令牌」**（自动打开浏览器，登录一次即可，无需 Node.js）
2. 手动复制：浏览器登录平台 → F12 → **Network** → 过滤 `pdf` → 点某个 `pdf` 请求 → 在 **Request Headers** 里复制 `x-nd-auth` 的完整值
3. 把令牌写入脚本同目录的 `token.txt`（工具会自动复用）

## 二、命令行

### 按链接 / contentId 下载

```bash
# 单个：传完整详情页链接 或 contentId
python smartedu_downloader.py "https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=xxxx&catalogType=tchMaterial&subCatalog=tchMaterial"

# 批量：命令行多个参数
python smartedu_downloader.py <链接或ID1> <链接或ID2> -o "D:/教材"

# 批量：从文本文件读取（每行一个链接或ID）
python smartedu_downloader.py -f links.txt -o ./downloads

# 不带参数 -> 交互模式，粘贴多行链接后按 空行 结束
python smartedu_downloader.py
```

### 按条件检索并下载

无需事先知道链接，直接按维度筛选目录：

```bash
python smartedu_downloader.py --phase 小学 --subject 语文 --grade 一年级 --list-only   # 先列出命中项
python smartedu_downloader.py --phase 小学 --publisher 人教版 --subject 数学 --all -o ./downloads  # 下齐命中全部
```

常用维度参数（`--publisher` 之外的维度均为**子串匹配**：查询词是维度取值的前缀或子串，如"统编"可命中"统编版"）：

| 参数 | 说明 | 常用取值示例 |
|------|------|--------------|
| `--phase` | 学段 | 小学 / 初中 / 高中 |
| `--grade` | 年级 | 一年级…（初一→七年级） |
| `--subject` | 科目 | 语文 / 数学 / 英语 / 科学… |
| `--publisher` | 版本 | 分组名 `人教版系`（= 人教版+统编版+人教鄂教版）、真实标签 `统编版`、简写 `人教` 均可 |
| `--volume` | 册次 | 上册 / 下册 |

> 平台把人教系的语文标成"统编版"、数学标成"人教版"、科学标成"人教鄂教版"，
> 所以想下全套人教主科时用 `--publisher 人教版系`，只想精确下某一版时用真实标签。

输出/令牌等：

```bash
-o, --output <目录>     下载保存目录（默认 ./downloads）
--token <令牌>          显式指定 x-nd-auth 令牌
--retries <n>           失败重试次数（默认 3）
--timeout <秒>          请求超时（默认 30）
--no-save-token         交互输入令牌时不写入 token.txt
--flat-name             用纯书名命名（默认结构化命名）
```

### 文件命名规则

默认采用**结构化命名**，文件名形如：

```
小学语文_统编版_一年级上册.pdf
小学数学_人教版_一年级上册.pdf
小学聋校道德与法治_统编版_一年级上册.pdf   # 特殊学校（聋校/盲校/培智）单列
小学道德与法治_统编版_五·四学制一年级上册.pdf  # 五·四学制与六三制是两套书，分开落盘
初中体育与健康_人教版_八年级全一册_教师用书篮球.pdf  # 标题里未入字段的「区分语」补在尾部
```

命名模板为 `{学段}[{特殊学校}]{科目}_{版本}[_{学制}]_{年级册次}[_{区分语}]`。版本、年级册次外的书名信息（教师用书 / 简谱 / 篮球 / 学生活动手册等）会作为**区分语**拼在尾部，避免「体育与健康教师用书·篮球」与「·足球」这类同名教材互相覆盖。字段缺失时自动省略对应段（不会出现空下划线），全部缺失时回退到清洗后的教材书名。

- 命令行加 `--flat-name` 可回退为「纯书名」命名（历史行为）。
- 网页界面在「下载任务」卡片勾选「纯书名命名」即可切回。

> 详情元数据缺失时，特殊学校与学制会从标题文本自动补提，保证盲校/聋校/培智及五·四学制教材不与普通教材撞名。

### 教材名称规范化

平台原始书名格式很不统一（大量冗余前缀、混用 `•`/`·`、中文间乱加空格、学制写法不一）。目录浏览与结果展示会先做**名称规范化**：

- 去掉「（根据2022年版课程标准修订）」「义务教育教科书」「普通高中教科书」等纯载体前缀
- 分隔符 `•` 统一为 `·`，去掉中文之间的多余空格
- 学制写法统一为「五·四学制」「六·三学制」，盲校/聋校/培智保留为书名前缀

原始书名保留在 `title_raw` 字段，两种写法都能搜到。

## 三、常见问题

- **401 / 403**：没有有效令牌，或令牌过期。请重新获取 `x-nd-auth`。若请求**详情 JSON 全端点均 403**，说明该资源已被平台下架。
- **目录缓存在旧**：目录数据缓存在同目录 `catalog_cache.json`（7 天自动过期），也可直接删除该文件强制刷新。
- **端口被占用**：GUI 会自动换端口，或用 `--port` 指定。
- **中文路径乱码（仅旧版）**：本工具已内置 UTF-8 控制台与目录选择编码修复，如仍异常请更新到最新版。

## 如何启用更多版本 / 学段

版本可见分组 `PUB_GROUPS`、学段开放范围 `SUPPORTED_SCOPE`、界面展示维度 `ENABLED_DIMS`
统一定义在 **`smartedu_downloader.py`** 顶部，命令行与网页界面共用同一份规则，
不会出现两处各改一次导致不一致。

要启用更多版本（如 北师大版、教科版…），往 `PUB_GROUPS` 加一行并重启 GUI：

```python
PUB_GROUPS = {
    "人教版系": ["人教版", "统编版", "人教鄂教版"],
    "沪教版":   ["沪教版"],
    "北师大版": ["北师大版"],      # ← 新增：界面立刻出现「北师大版」
}
```

分组的渲染规则：

- 组内**多于一个**标签 → 渲染成「分组（整套选）+ 组内真实标签（精确选）」两级
- 组内**只有一个**标签 → 直接作为顶层选项，不制造冗余的两层

`SUPPORTED_SCOPE` 限定某维度的开放范围（如 `"phase": ["小学"]`），范围外的取值在
下拉里显示为"暂未开放"且不可选；若某维度只开放了一个取值，界面首次进入会
自动替用户选中它（由 `DEFAULT_FILTERS` 派生），避免首屏就是几千条混杂结果。

> 命令行（CLI）不受 `PUB_GROUPS` / `SUPPORTED_SCOPE` 限制，`--publisher` 可直接传任意版本标签。

## 开发笔记（重新打包前必读）

这一节记录的是**打包过程中真实踩过的坑**。如果哪天本地环境全丢了，从仓库 clone 下来后
先看这里，能省掉几小时的重复排查。

### 1. 清理 `dist/` / `build/` 删不掉

某些环境（如带「安全删除」钩子的 AI 编程工具）会把 `rm` / `os.remove` / `shutil.rmtree` /
PowerShell `Remove-Item` 重定向到回收站工具。该工具遇到**中文路径**时会出现编码乱码，
删除失败后又拒绝回退到直接删除，于是目录永远清不掉。

**解法**：绕过这些命令，直接调 .NET 原生 API：

```powershell
[System.IO.Directory]::Delete("D:\path\to\dist\书到了", $true)   # 删目录
[System.IO.File]::Delete("D:\path\to\dist\书到了.zip")            # 删文件
```

### 2. exe 属性里版本信息全空

- **坑一**：`EXE(version=...)` 传的是**版本文件路径字符串**，不是文件内容
  （PyInstaller 内部会 `open(filename)` 自己读）。传内容字符串会报 `FileNotFoundError`。
- **坑二（真正的元凶）**：`version_info.txt` 里 `StringTable` 的语言代码必须与
  `VarFileInfo` 的 `Translation` 一致。本项目 Translation 是 `[1033, 1200]`（英文），
  所以 StringTable 键必须是 `040904b0`（英文 0x0409）。若写成 `080404b0`（中文 0x0804），
  资源表不对齐，Windows 按英文读取时字段全部为空——**打包日志却照样显示
  "Copying version information to EXE"，极具迷惑性**。

### 3. 多实例端口冲突（曾导致"改了中文名就用不了"的假象）

`ThreadingHTTPServer` 默认 `allow_reuse_address=1`，重复双击 exe 时多个进程会**静默共用
同一个 8765 端口**——不报错、不提示，但请求被随机分发到不同实例，表现为界面点下载没反应、
进度条不动。**这不是中文文件名的问题。**

修复方式（`smartedu_downloader_gui.py`）保留了三处，改动时务必保留：

| 组件 | 作用 |
|------|------|
| `LocalServer.allow_reuse_address = 0` | 端口独占，重复启动直接失败而非静默共用 |
| `_probe_existing()` | 启动前探测已有实例，命中则复用其窗口 + 弹提示后退出 |
| `_notify()` | windowed 模式无控制台，必须弹框，否则用户以为程序没反应 |

`_probe_existing()` 会绕开系统代理直连 `127.0.0.1`，且只认本程序的特征响应，
不会把占用同端口的其他程序误判为自己的实例。

### 4. 测试本地服务时的三个陷阱（会测出假故障）

1. **绕开系统代理**：`urllib.request` 默认读取系统代理，本机开着代理时访问
   `127.0.0.1:8765` 会被代理拦截返回 **502**。必须
   `urllib.request.build_opener(urllib.request.ProxyHandler({}))`。
2. **脱离进程组启动**：用 `subprocess.Popen(..., creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`，
   否则后台进程随测试命令结束被回收，下一条命令再测就是 `ConnectionRefused`。
3. **别用 `tasklist | grep 中文进程名`**：中文在管道里编码会错乱，必失败，容易误判
   "exe 没启动"。改用 `tasklist /FI "PID eq <pid>"`。

### 5. 其他约定

- 装依赖用 `python -m pip install`，**不要用裸 `pip`**（可能指向别的 Python 版本）。
- 令牌抓取已整合进 GUI 进程内（`auto_fetch_token.run_token_fetch`），
  **不要改回 subprocess 调外部 Python**——那样对方机器就必须装 Python。
- 筛选维度规则统一定义在 `smartedu_downloader.py` 顶部，CLI 与 GUI 共用。
- 新增维度排序表时记得同步注册进 `DIM_ORDERS`。

## 版本与发布规范

遵循语义化版本（`主.次.修订`），以「用户感知」为判断依据：

| 级别 | 触发条件 | 示例 |
|---|---|---|
| 主版本 +1 | 有兼容断裂或重大取舍：移除平台支持、文件命名规则不兼容变更、砍掉核心使用方式、界面彻底重做 | 1.x → 2.0.0 |
| 次版本 +1 | 有新能力且老用法不受影响：新功能、新平台支持、成块的界面改版 | 跨平台支持（1.2.0） |
| 修订号 +1 | 只修不增：bug 修复、安全加固、CI/构建链调整、依赖与文档更新 | 本地服务安全加固（1.1.1） |

- 预发布用 `vX.Y.Z-beta.N` 标注并在 Release 勾选 pre-release；安全紧急修复走修订号单独发版
- 发版固定动作：改 `version_info.txt`（版本号单一数据源）→ 同步 `smartedu_webui.html` 里的兜底版本号 → 提交 → 推送 `vX.Y.Z` 标签，CI 自动构建三平台产物并挂到 Release

## 免责声明

本工具仅供个人学习、研究、教学备课使用。教材版权归国家中小学智慧教育平台及相应出版机构所有，请勿用于商业用途或二次分发。

本项目采用 [PolyForm Noncommercial 1.0.0](LICENSE) 许可：个人学习、研究、教学等**非商业用途**可自由使用、修改与分发；**任何商业用途（含盈利性分发、打包转售）均需另行获得作者授权**。

> **关于公开分发的特别提醒**：如将本工具通过网盘、GitHub 等渠道公开提供给不特定人群，
> 属于向公众提供下载版权教材的渠道，法律风险显著高于「个人自用 + 熟人」场景，请自行
> 评估并承担相应责任。建议在分发页面显著标注「仅供学习研究、请勿商用」，并保留本免责声明。
>
> **杀软误报说明**：本工具采用 PyInstaller 打包且未做数字签名，部分杀毒软件（尤其
> Windows Defender、360 等）可能误报。如遇拦截，请对方用户选择「信任/允许」或添加
> 白名单；这是 PyInstaller 打包程序的通病，并非病毒。若要彻底消除误报，需购买代码
> 签名证书（EV 证书，价格较高），一般个人分发不建议。
