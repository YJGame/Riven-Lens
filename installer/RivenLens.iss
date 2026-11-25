; Inno Setup 6 Script for 裂罅透镜 (Riven Lens)
; Recommended: run powershell -ExecutionPolicy Bypass -File installer\build_windows_release.ps1
; (it stages a clean copy without personal settings/logs and auto-invokes ISCC.exe).
; Or compile directly against dist\裂罅透镜 with:
;   ISCC.exe /DStageDir=..\dist\裂罅透镜 installer\RivenLens.iss

#define MyAppName "裂罅透镜 (Riven Lens)"
#define MyAppShortName "裂罅透镜"
#define MyAppVersion "3.0.0"
#define MyAppPublisher "Riven Lens"
#define MyAppExeName "裂罅透镜.exe"
#ifndef StageDir
  #define StageDir "..\build\_win_release_stage\裂罅透镜"
#endif

[Setup]
AppId={{B4E2D9C1-7F8A-4D3E-9C1B-5A6E8F0D2C4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\RivenLens
DefaultGroupName={#MyAppShortName}
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\release
OutputBaseFilename=裂罅透镜_Setup_v{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式 (&D)"; GroupDescription: "附加任务:"
Name: "desktopadminicon"; Description: "创建管理员启动桌面快捷方式（若游戏以管理员权限运行）"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{#MyAppShortName}（管理员启动）"; Filename: "{app}\启动悬浮窗_管理员.bat"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppShortName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autodesktop}\{#MyAppShortName}（管理员）"; Filename: "{app}\启动悬浮窗_管理员.bat"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopadminicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppShortName}"; Flags: nowait postinstall skipifsilent
