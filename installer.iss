; Inno Setup script - build via build_windows.ps1
#define AppName "Video Transcriber"
#define AppVer "1.0.0"
#define AppExe "VideoTranscriber.exe"

[Setup]
AppId={{8C1F6B2E-3A4D-4E5F-9A1B-2C3D4E5F6A7B}
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\VideoTranscriber
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; per-user install without admin by default; user may choose all-users in a dialog
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=VideoTranscriber-Setup-{#AppVer}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\VideoTranscriber\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
