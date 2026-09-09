; 书到了（ShudaoLe）Windows 安装脚本（Inno Setup 6）
; 由 CI（release.yml）在 PyInstaller 产物 dist\书到了\ 上执行：
;   ISCC.exe /DVersion=x.y.z installer.iss
; 产物：ShudaoLe-<version>-setup.exe（开始菜单/桌面快捷方式/标准卸载器）

#ifndef Version
#define Version "0.0.0"
#endif

#define AppName "书到了"
#define AppNameEn "ShudaoLe"
#define AppExeName "书到了.exe"
#define Publisher "tau625"
#define RepoURL "https://github.com/tau625/ShudaoLe"

[Setup]
AppId={{8C1C4E2A-95D3-4B7A-9E0F-SHUDAOLE01}
; 程序启动时创建同名互斥量（server.py _acquire_app_mutex），
; 安装/升级时检测到则弹出「请先关闭书到了」的友好提示，而非文件占用报错
AppMutex=ShudaoLeAppMutex
AppName={#AppName}
AppVersion={#Version}
AppVerName={#AppName} {#Version}（{#AppNameEn}）
AppPublisher={#Publisher}
AppPublisherURL={#RepoURL}
AppSupportURL={#RepoURL}/issues
DefaultDirName={autopf}\{#AppNameEn}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName}（{#AppNameEn}）
OutputBaseFilename=ShudaoLe-{#Version}-setup
; 产物输出到脚本所在目录（Inno 默认 Output\ 子目录，CI 校验与上传均按仓库根目录找）
OutputDir=.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; 未签名（个人项目暂无证书）：关闭 UAC 数字签名校验提示的强提醒，
; 但保留 PrivilegesRequired=lowest 可装到用户目录，减少管理员弹窗
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
LicenseFile=LICENSE
InfoBeforeFile=installer-notice.txt

[Languages]
; runner 自带的 Inno Setup 不含简体中文（非官方语言），随仓库捆绑
; （取自 jrsoftware/issrc is-6_7_1 tag，UTF-8 版式，需 Inno 6.5+）
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "downloadsfolder"; Description: "在「下载」文件夹创建教材保存子目录（书到了教材）"; GroupDescription: "附加选项："

[Files]
; onedir 全量搬运（exe + _internal）
Source: "dist\书到了\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Dirs]
Name: "{autodocs}\书到了教材"; Tasks: downloadsfolder

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理程序目录内我们生成的缓存/日志，绝不碰用户下载目录
Type: files; Name: "{app}\catalog_cache.json"
