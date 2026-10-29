; Inno Setup script for Camera Wire Tool (Windows installer).
; Built in CI after PyInstaller produces dist\CameraWireTool.exe:
;   ISCC.exe /DMyAppVersion=1.2.4 installer\windows.iss
; The app is a single self-contained exe (Python, Tk, sv_ttk, reportlab all
; bundled), so the installer just places it, makes shortcuts, and registers
; an uninstaller. Output: installer\installer_out\
;   CameraWireTool-<version>-windows-x64-setup.exe

#ifndef MyAppVersion
  #define MyAppVersion "1.2.4"
#endif

#define MyAppName "Camera Wire Tool"
#define MyAppExe "CameraWireTool.exe"
#define MyAppPublisher "Deenyoro"

[Setup]
; AppId is how Windows and Inno Setup recognise an existing install for
; upgrades and uninstall. It was generated once (uuid4) for this app and must
; NEVER change across releases: changing it would orphan every earlier
; install (two entries in Apps & features, upgrades installing side by side).
AppId={{EA0F1854-F0D3-49E5-8644-CD137925AA04}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Install for the current user by default (no UAC prompt); the user can pick
; "all users" in a dialog, or pass /ALLUSERS or /CURRENTUSER on the command
; line. {autopf}, {group} and {autodesktop} follow that choice.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_out
OutputBaseFilename=CameraWireTool-{#MyAppVersion}-windows-x64-setup
SetupIconFile=..\packaging\icons\cameramtool.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
