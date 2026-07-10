; Inno Setup script for the PDF Toolkit Windows installer.
;
; Requires Inno Setup 6.3+ (free): https://jrsoftware.org/isinfo.php
; Build the app first (python -m PyInstaller pdf_toolkit.spec --noconfirm),
; then compile this script -- or just run build_installer.bat, which does
; both. Output: dist\PDFToolkit-Setup-<version>.exe
;
; Uninstall note: the uninstaller removes {app} only. Per-user data is
; deliberately left in place (settings/logs under %APPDATA%\PDFCompress,
; the on-demand translation runtime under %LOCALAPPDATA%\PDFToolkit, and
; Argos language packs in their own per-user dir) so reinstalls/upgrades
; keep the user's setup.

#define MyAppName "PDF Toolkit"
; Keep in sync with app.py VERSION -- see CLAUDE.md's version-bump list.
#define MyAppVersion "4.23"
#define MyAppPublisher "FR3T0T"
#define MyAppURL "https://github.com/FR3T0T"
#define MyAppExeName "PDFToolkit.exe"

[Setup]
; Fixed GUID identifying this app across releases (never change it --
; it's how upgrades find the existing install).
AppId={{8B1F4C67-3D0A-4E52-9A1B-6C2E85D47F91}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; All-users install into Program Files.
PrivilegesRequired=admin
; 64-bit app: install under Program Files (not x86) on 64-bit Windows.
; (x64compatible needs Inno Setup 6.3+.)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=PDFToolkit-Setup-{#MyAppVersion}
SetupIconFile=pdf_toolkit.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\PDFToolkit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Standalone copy of the icon for the shortcuts below.
Source: "pdf_toolkit.ico"; DestDir: "{app}"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\pdf_toolkit.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\pdf_toolkit.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
