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
- **ManifestVersion** 固定 1.9.0；如果 winget-pkgs 审核bot 提示升级 schema，
  改 `gen_manifests.py` 里的 `MANIFEST_VERSION` 重新生成即可。
- **首次收录**会走单独的 `Add package` PR；之后每个新版本一个 `Add version` PR。
