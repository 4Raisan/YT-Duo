; Build with Inno Setup 6. Installed state lives outside the application folder.
#define AppVersion "4.6.0"
#ifndef PortableExePath
#define PortableExePath "..\build\release-stage\windows\YT-Duo-Portable.exe"
#endif
[Setup]
AppId={{FDE2B3BE-6873-435B-9FB7-28CE550C2BC0}
AppName=YT-Duo
AppVersion={#AppVersion}
AppPublisher=4Raisan
AppPublisherURL=https://github.com/4Raisan/YT-Duo
AppSupportURL=https://github.com/4Raisan/YT-Duo/issues
DefaultDirName={localappdata}\Programs\YT-Duo
DefaultGroupName=YT-Duo
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\build\release-stage\windows
OutputBaseFilename=YT-Duo-Setup
SetupIconFile=..\src\ytduo\assets\icon.ico
UninstallDisplayIcon={app}\YT-Duo.exe
WizardStyle=modern
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#PortableExePath}"; DestDir: "{app}"; DestName: "YT-Duo.exe"; Flags: ignoreversion
Source: "installed.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\YT-Duo"; Filename: "{app}\YT-Duo.exe"
Name: "{autodesktop}\YT-Duo"; Filename: "{app}\YT-Duo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\YT-Duo.exe"; Description: "Launch YT-Duo"; Flags: nowait postinstall skipifsilent
