# winget 清单（winget-pkgs 提交用）

让用户能 `winget install tau625.ShudaoLe` 一行装机。winget 是 Windows 10/11 自带的
官方包管理器，上目录（microsoft/winget-pkgs）意味着「去哪下载 / 是不是正版」由微软
目录背书——对非技术用户（老师、家长）是最大的信任加分项。

## 目录结构

```
packaging/winget/
├── gen_manifests.py    # 清单生成器，每次发版后跑一次
├── README.md           # 本文件
└── manifests/
    └── t/tau625/ShudaoLe/<version>/   # 与 winget-pkgs 仓库路径完全同构
        ├── tau625.ShudaoLe.yaml
        ├── tau625.ShudaoLe.installer.yaml
        └── tau625.ShudaoLe.locale.en-US.yaml
```

## 每次发版后：生成新版本清单

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

winget 目录不走 API，靠 PR 合入。完整流程：

```powershell
# 1. fork https://github.com/microsoft/winget-pkgs 到自己账号下，然后：
git clone https://github.com/tau625/winget-pkgs
cd winget-pkgs

# 2. 把生成好的清单原样拷进 manifests/
xcopy /E /I /Y "D:\01_Projects\smartedu-教材下载器\packaging\winget\manifests\t" manifests\t\

# 3. 分支 + 提交（commit message 有格式要求，bot 会检查）
git checkout -b tau625.ShudaoLe-1.3.8
git add manifests/t/tau625/ShudaoLe
git commit -m "Add version: tau625.ShudaoLe version 1.3.8"
git push -u origin tau625.ShudaoLe-1.3.8

# 4. 到 GitHub 上对 microsoft/winget-pkgs 的 master 开 PR，等自动校验 + 人工审核
```

提交前可本地校验清单合法性（Windows）：

```powershell
winget validate "D:\01_Projects\smartedu-教材下载器\packaging\winget\manifests\t\tau625\ShudaoLe\1.3.8"
```

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
- 提交前本地自测：
  `winget validate <清单目录>`；更彻底用 winget-pkgs 的 `Tools\SandboxTest.ps1`
  在 Windows Sandbox 里实际静默装一遍。

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
