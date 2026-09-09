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
; 程序启动时创建同名互斥量（server.py _acquire_app_mutex）。
; 兜底检测：若 [Code] 里的 KillRunningApp 没杀干净（如权限异常），
; 走到这一步会弹「请先关闭书到了」提示，而不是文件占用的生硬报错
AppMutex=ShudaoLeAppMutex
; 不用 Inno 自带的重启管理器关应用：本程序无 Win32 窗口（界面在浏览器），
; RM 发 WM_CLOSE 收不到，对话框里「关闭程序」按钮永远杀不掉，徒增困惑。
; 关闭/结束统一由 [Code] 的 KillRunningApp 强杀完成
CloseApplications=no
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

[Code]
// 安装前 / 卸载前强制结束运行中的书到了：
// 程序无 Win32 窗口（界面在浏览器里），taskkill 不带 /F 发 WM_CLOSE 收不到，
// 只能强杀；下载任务有持久化（tasks.json），强杀无损，下次启动会提示续传。
// 无条件按映像名杀两轮——旧版本（≤1.3.1）不创建互斥量，不能靠 AppMutex 判断在不在跑。
procedure KillRunningApp();
var
  R: Integer;
  I: Integer;
begin
  for I := 1 to 2 do
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'),
      '/f /t /im "{#AppExeName}"', '', SW_HIDE, ewWaitUntilTerminated, R);
    Sleep(600);
  end;
end;

function InitializeSetup(): Boolean;
begin
  KillRunningApp();
  Result := True;  // 返回 False 会中止安装；此处只杀进程，继续正常流程
end;

function InitializeUninstall(): Boolean;
begin
  KillRunningApp();
  Result := True;  // 返回 False 会中止卸载；此处只杀进程，继续正常流程
end;
