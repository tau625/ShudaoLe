# winget 清单（winget-pkgs 提交用）

让用户能 `winget install tau625.ShudaoLe` 一行装机。winget 是 Windows 10/11 自带的
官方包管理器，上目录（microsoft/winget-pkgs）意味着「去哪下载 / 是不是正版」由微软
目录背书——对非技术用户（老师、家长）是最大的信任加分项。

## 目录结构

```
packaging/winget/
├── gen_manifests.py    # 清单生成器，每次发版后跑一次
├── submit_pr.sh        # 零克隆提 PR（国内网络专用，走 GitHub API）
├── README.md           # 本文件
└── manifests/
    └── t/tau625/ShudaoLe/<version>/   # 与 winget-pkgs 仓库路径完全同构
        ├── tau625.ShudaoLe.yaml
        ├── tau625.ShudaoLe.installer.yaml
        └── tau625.ShudaoLe.locale.en-US.yaml
```

## 每次发版后：生成新版本清单

> ⚠️ **顺序不能反：必须等 Release 把 `ShudaoLe-<version>-setup.exe` 挂完之后，再跑生成器。**
>
> PyInstaller 产物不可复现——本地构建的安装包与 CI 传到 Release 的那个 SHA256 不同。
> v1.5.0 的清单就是在 Release 挂出前 50 分钟生成的，`InstallerSha256` 记的是本地产物的
> 哈希（`352c34…`），而 Release 上实际是 `1a624b…`；这样提交必定被 winget-pkgs 的
> hash 校验打回。生成器现已改为**默认只认 Release 的值**，本地文件只在显式传 `--file`
> （发版前预生成草稿）时才用。

```powershell
# 版本号自动从 version_info.txt 读取；SHA256 自动从 Release 的 SHA256SUMS.txt 获取
python packaging/winget/gen_manifests.py --fetch
```

也可以手动指定：

```powershell
python packaging/winget/gen_manifests.py --sha256 <hex>      # 手动给哈希
python packaging/winget/gen_manifests.py --file <setup.exe>  # 对本地文件现算
```

前提：该版本的 Release 必须已经挂上 `ShudaoLe-<version>-setup.exe`
（CI 自动上传，附件名由 installer.iss 的 `OutputBaseFilename` 决定）。

## 提交 PR 到 microsoft/winget-pkgs

> ⚠️ **首次收录只提交最新版本（1.5.0）**，不要把 `manifests/t/tau625/ShudaoLe/` 下的
> 六个历史版本目录一起塞进去——winget-pkgs 规定「一个 PR 只改一个包的一个版本目录」，
> 多版本会直接被 bot 拒。历史版本清单留在仓库里当存档就够，不需要提交。
> 收录成功后再发新版，才是每次一个 `Add version` PR。

### ⚠️ 国内网络：别 clone，走 API（已实测）

网上教程都让你 `git clone https://github.com/microsoft/winget-pkgs`，**国内这条路走不通**：

| 操作 | 实测结果 |
|---|---|
| `git ls-remote`（小请求） | ✅ 1.65 s |
| `git push`（小 pack） | ✅ 正常 |
| GitHub REST API | ✅ 正常 |
| **clone 大 pack 下载** | ❌ **~8.5 KB/s** |

winget-pkgs 的历史有 **866 MB**，实测拉了 5 分钟只到 **5.0 MB**，之后长时间零增量——
`--depth 1 --filter=blob:none --sparse` 同样卡死（`tmp_pack` 停在 5 MB 不动）。
卡的只是**大 pack 下载**，别的都通。

所以用 `submit_pr.sh`，全程 REST API，总流量几十 KB：

```bash
# 1. 体检 + 打印计划（不产生任何 GitHub 操作）
bash packaging/winget/submit_pr.sh --dry-run

# 2. 真提交：自动 fork → 建分支 → 上传 3 个清单 → 开 PR
bash packaging/winget/submit_pr.sh

# 也可以指定版本
bash packaging/winget/submit_pr.sh 1.5.0
```

脚本做的事（**幂等，可放心重跑**——会先删同名分支再重建）：

1. `gh repo view` 查 fork；没有就 `gh repo fork --clone=false`（服务端复制，不下载到本地）
2. `gh repo sync --branch master` 把 fork 对齐上游
3. 建分支 `tau625.ShudaoLe-<version>`（基于 fork 的 master）
4. Contents API 逐个 PUT 清单文件（每个文件一个 commit，合并时 squash 无妨）
5. `gh pr create` 开 PR，标题 `New package: tau625.ShudaoLe version <version>`

`--dry-run` 会顺带跑 `winget validate`（用 `cygpath` 把 Git Bash 的 `/d/...` 转成
winget 认的 `D:\...`），清单不合法会直接中止。

### 备选：网页端手动建文件（零工具链）

不想用脚本时，浏览器里同样能做，也不下载仓库：

1. 打开 https://github.com/microsoft/winget-pkgs/fork 建 fork（服务端操作，秒级）
2. 进自己 fork 的页面 → **Add file → Create new file**
3. 文件名框里填**完整路径** `manifests/t/tau625/ShudaoLe/1.5.0/tau625.ShudaoLe.yaml`（斜杠会自动变成目录）
4. 把本地同名文件的**全部内容**粘进去 → `Commit changes`
5. 另外两个文件重复 3~4 步
6. 回 microsoft/winget-pkgs，点 **Compare & pull request** 开 PR

需要建的三个文件：

```
manifests/t/tau625/ShudaoLe/1.5.0/tau625.ShudaoLe.yaml
manifests/t/tau625/ShudaoLe/1.5.0/tau625.ShudaoLe.installer.yaml
manifests/t/tau625/ShudaoLe/1.5.0/tau625.ShudaoLe.locale.en-US.yaml
```

### 其它注意

- **PR 只能来自 fork**，不能直接往 `microsoft/winget-pkgs` 推。
- 想手动校验清单（Windows PowerShell）：
  `winget validate "D:\01_Projects\smartedu-教材下载器\packaging\winget\manifests\t\tau625\ShudaoLe\1.5.0"`
- 提交后自动验证 + 人工审核都跑在**微软的机器**上（校验 schema、把 InstallerUrl 下载下来
  比对 Sha256、恶意软件扫描），跟你的国内网络无关，不用挂代理等结果。

## 实现细节备忘（改代码前先读）

- **ARP 匹配**：安装器是 Inno（无 MSI ProductCode），winget 靠
  `AppsAndFeaturesEntries.DisplayName` + `Publisher` 匹配已安装应用。
  DisplayName 必须与 installer.iss 的 `UninstallDisplayName` 一字不差——
  当前是 `书到了（ShudaoLe）`（全角括号）。改了那边的显示名就同步改
  `gen_manifests.py` 里的 `ARP_DISPLAY_NAME`，否则升级检测失效。
- **升级**：`InstallerSwitches` 声明了 `/VERYSILENT`，winget 升级时静默跑安装器，
  与应用内自动更新（update.py → 静默安装）互不冲突。
- **ManifestVersion** 固定 1.12.0（winget-pkgs 审核推荐版本，1.10.0 也接受；
  如 bot 提示升级 schema，改 `gen_manifests.py` 里的 `MANIFEST_VERSION` 重新生成即可）。
- **License 必须写 SPDX 标识符**：`PolyForm-Noncommercial-1.0.0`（连字符版）。
  写成 `PolyForm Noncommercial 1.0.0` 能过 winget 的本地 schema 校验，但它不是合法
  SPDX 表达式，人工审核会被挑。
- **`Scope: machine` 和 `ElevationRequirement: elevationRequired` 是两个维度，都要写**：
  前者是安装范围（`winget install --scope machine/user` 靠它过滤），后者是提权需求。
  只写后者时，按 scope 筛选的安装命令匹配不到这个包。
- **静默参数要写全**：一旦清单里给了 `InstallerSwitches.Silent`，winget 就不再叠加
  自己的默认值。所以 `/SUPPRESSMSGBOXES`、`/SP-` 得自己带上（当前值与 winget 对 inno
  的默认值一致）。
- **首次收录**会走单独的 `Add package` PR；之后每个新版本一个 `Add version` PR。

## 限制与前提条件（提交前自查）

- **必须支持静默安装**——winget 目录的硬性要求，没有静默模式就不能收录。
  Inno 自带 `/SILENT` `/VERYSILENT`，本项目天然满足。
- **不接受脚本安装器**（.bat / .ps1），只收 MSIX / MSI / APPX / EXE / 字体。本项目用 Inno exe。
- **InstallerUrl 必须稳定、版本化、永久可达**，指向官方发布源。GitHub Release 直链满足；
  注意别把 URL 指到会过期的构建缓存或网盘。
- **一个 PR 只能改一个包的一个版本**，不要把多个版本/多个包塞进一个 PR。
- 提交前先查重：`winget search shudaole`、GitHub 搜 `manifests/t/tau625`、翻一遍 open PR。
- 首次 PR 需要签微软 CLA（PR 页面上 bot 会提示，网页里点一下即可）。
- 提交后自动验证管线会校验：清单 schema、InstallerSha256 与 URL 文件一致、
  恶意软件扫描；通过后还有人工审核（一般 1~7 天）。微软保留以任何理由拒绝的权利。
- 提交前本地自测，两步：
  1. `bash packaging/winget/submit_pr.sh --dry-run`——自动跑 `winget validate` + 全链路体检；
  2. 更彻底一点，真的装一遍（需要 UAC，会写进 Program Files）：
     ```powershell
     winget settings --enable LocalManifestFiles
     cd D:\01_Projects\smartedu-教材下载器\packaging\winget\manifests\t\tau625\ShudaoLe\1.5.0
     winget install --manifest .
     winget list --id tau625.ShudaoLe     # 能列出来才说明 ARP 匹配对了
     winget uninstall --id tau625.ShudaoLe
     ```
  再彻底就用 winget-pkgs 的 `Tools\SandboxTest.ps1` 在 Windows Sandbox 里静默装一遍。

## 同名与所有权：别人能抢注吗？

- **PackageIdentifier 全局唯一、先到先得。** `tau625.ShudaoLe` 合入目录后，
  任何人再提交相同标识符会被 bot 以「identifier already exists」直接拒绝——不存在重名包。
- **但任何人都可以给已收录的包提交新版本 PR**（目录是社区维护的，不限作者本人）。
  防线在审核：新版本 PR 必须给出新哈希、URL 变更会被重点审查，自动验证 + 人工审核两道关。
- **本项目的天然优势**：标识符以 GitHub 用户名 `tau625` 开头、安装器 URL 指向
  `tau625/ShudaoLe` 的 Release、PR 由 `tau625` 提交——三者一致，冒充几乎不可能通过审核。
- 真出现冒名或恶意改动（如有人把 URL 换成钓鱼源）：到 microsoft/winget-pkgs 开 issue
  举证（仓库归属 + Release 页面），微软会下架/转移标识符，官方作者申诉优先。
- 结论：**没有实质风险，尽快提 PR 占住标识符即可**；即便万一被抢注，
  官方作者凭仓库所有权可以申诉收回。
