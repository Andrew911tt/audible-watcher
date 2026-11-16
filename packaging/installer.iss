; Inno Setup script for the Windows installer. Built by packaging\build_windows.ps1
; or the GitHub release workflow:  ISCC /DAppVersion=1.2.3 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{E3363609-0733-4481-A374-B2837191DC8D}
AppName=Audible Watcher
AppVersion={#AppVersion}
AppPublisher=Audible Watcher contributors
AppPublisherURL=https://github.com/Andrew911tt/audible-watcher
DefaultDirName={autopf}\Audible Watcher
DefaultGroupName=Audible Watcher
DisableProgramGroupPage=yes
; per-user install: no admin prompt, installs under %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=AudibleWatcher-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Audible Watcher.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Tasks]
Name: "startup"; Description: "Start Audible Watcher when I sign in to Windows (needed for the daily check)"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "..\dist\Audible Watcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Audible Watcher"; Filename: "{app}\Audible Watcher.exe"
Name: "{group}\Uninstall Audible Watcher"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Audible Watcher"; Filename: "{app}\Audible Watcher.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Audible Watcher"; ValueData: """{app}\Audible Watcher.exe"" --background"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\Audible Watcher.exe"; Description: "Open Audible Watcher now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ""Audible Watcher.exe"""; Flags: runhidden; RunOnceId: "StopApp"

; Your data (%LOCALAPPDATA%\Audible Watcher) is left in place on uninstall.
