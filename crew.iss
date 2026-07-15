; Crew · Inno Setup 脚本
; 编译：ISCC.exe crew.iss  →  产出 dist\Crew-Setup-v1.1.exe

#define AppName        "Crew"
#define AppVersion     "1.7.0"
#define AppPublisher   "Crew"
#define AppExeName     "crew.exe"
#define SourceDir      "dist\Crew"

[Setup]
; 基本信息
AppId={{7C8B4E1A-9D3F-4A5C-B2E8-CREW-1100-2026}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppComments=多 Agent 团队群聊 · 11 位英文名同事 + Foreman 任务调度 + Hermes 执行
AppSupportURL=https://github.com/
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=
InfoBeforeFile=
InfoAfterFile=

; 用户级安装（无需管理员）；写入 %LOCALAPPDATA%\Programs\Crew\
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 输出
OutputDir=dist
OutputBaseFilename=Crew-Setup-v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; 卸载器
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}

; 安装器自身的图标 & 界面图片
SetupIconFile=static\crew.ico

; 界面
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked
Name: "startmenuicon"; Description: "创建开始菜单快捷方式"; GroupDescription: "附加快捷方式："

[Files]
; 打包整个 dist\Crew\ 目录（含 crew.exe + _internal/ + README）
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单（可选）
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startmenuicon
Name: "{autoprograms}\卸载 {#AppName}"; Filename: "{uninstallexe}"; Tasks: startmenuicon

; 桌面（可选）
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; 装完询问是否立即启动
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清可能生成的运行时文件（不删用户数据 %APPDATA%\Crew\）
Type: files; Name: "{app}\*.log"
