; CamAndroid Windows installer (Inno Setup 6).
; Installs camandroid.exe, registers the CamAndroid virtual camera for all users (64- and 32-bit
; apps), ships adb for USB connections and opens the firewall for phone discovery (mDNS).
;
; Build: iscc /DAppVersion=1.2.3 /DSrcDir=... /DVcamDir=... /DAdbDir=... /DIconFile=... camandroid.iss

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef SrcDir
  #define SrcDir "..\..\desktop\dist"
#endif
#ifndef VcamDir
  #define VcamDir "..\vcam\dist"
#endif
#ifndef AdbDir
  #define AdbDir "..\adb"
#endif
#ifndef OutDir
  #define OutDir "..\..\desktop\out"
#endif
#ifndef IconFile
  ; Written by desktop/camandroid.spec (same icon as the Android app).
  #define IconFile "..\..\desktop\build\camandroid.ico"
#endif
; File version needs four numbers; "0.3.41-dev" -> "0.3.41.0".
#define NumericVersion Copy(AppVersion, 1, Pos("-", AppVersion + "-") - 1) + ".0"

[Setup]
AppId={{5C7A4F0B-2D1E-4B8A-9F36-7E1C2A9D4B51}
AppName=CamAndroid
AppVersion={#AppVersion}
AppPublisher=Omar Kharrat
AppPublisherURL=https://github.com/smayah69/CamAndroid
AppSupportURL=https://github.com/smayah69/CamAndroid/issues
AppUpdatesURL=https://github.com/smayah69/CamAndroid/releases
AppCopyright=Copyright (C) 2026 Omar Kharrat (@smayah69)
AppComments=Use your Android phone as a webcam
VersionInfoVersion={#NumericVersion}
VersionInfoCompany=Omar Kharrat
VersionInfoDescription=CamAndroid installer
VersionInfoCopyright=Copyright (C) 2026 Omar Kharrat (@smayah69)
VersionInfoProductName=CamAndroid
SetupIconFile={#IconFile}
DefaultDirName={autopf}\CamAndroid
DefaultGroupName=CamAndroid
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=CamAndroid-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\camandroid.exe
CloseApplications=yes
RestartIfNeededByRun=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SrcDir}\camandroid.exe"; DestDir: "{app}"; Flags: ignoreversion
; The camera DLLs are loaded by video apps; if one is running during an upgrade the old file is
; replaced at the next restart.
Source: "{#VcamDir}\camandroid-vcam64.dll"; DestDir: "{app}\vcam"; Flags: ignoreversion regserver 64bit restartreplace uninsrestartdelete
Source: "{#VcamDir}\camandroid-vcam32.dll"; DestDir: "{app}\vcam"; Flags: ignoreversion regserver 32bit restartreplace uninsrestartdelete
Source: "{#AdbDir}\*"; DestDir: "{app}\adb"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\CamAndroid"; Filename: "{app}\camandroid.exe"
Name: "{autodesktop}\CamAndroid"; Filename: "{app}\camandroid.exe"; Tasks: desktopicon

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""CamAndroid phone discovery"" dir=in action=allow program=""{app}\camandroid.exe"" protocol=UDP localport=5353 profile=private,domain"; Flags: runhidden; StatusMsg: "Allowing phone discovery on private networks..."
Filename: "{app}\camandroid.exe"; Description: "Start CamAndroid"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""CamAndroid phone discovery"""; Flags: runhidden; RunOnceId: "firewall"
Filename: "{app}\adb\adb.exe"; Parameters: "kill-server"; Flags: runhidden skipifdoesntexist; RunOnceId: "adb"

[UninstallDelete]
Type: dirifempty; Name: "{app}\vcam"
Type: dirifempty; Name: "{app}"
