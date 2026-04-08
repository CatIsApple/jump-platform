; GUARDIAN Jump Worker Dashboard - Inno Setup Script
; 설치 시 Windows Defender 예외 자동 등록

#define AppName "GUARDIAN"
#define AppVersion "0.3.0"
#define AppPublisher "GUARDIAN"
#define AppExeName "jump-worker-dashboard.exe"

[Setup]
AppId={{B5F3A2D1-7C4E-4A8B-9D6F-1E2C3B4A5D6E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename=GUARDIAN_Jump_Setup
Compression=lzma2/ultra64
SolidCompression=yes
; 관리자 권한 필요 (Defender 예외 등록)
PrivilegesRequired=admin
SetupIconFile=..\assets\calendar.ico
UninstallDisplayName={#AppName} 점프 프로그램
UninstallDisplayIcon={app}\assets\calendar.ico
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"

[Files]
; PyInstaller 빌드 출력물 전체 복사
Source: "..\dist\jump-worker-dashboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\calendar.ico"
Name: "{autodesktop}\{#AppName} 점프 프로그램"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\calendar.ico"; Tasks: desktopicon
Name: "{group}\{#AppName} 제거"; Filename: "{uninstallexe}"

[Run]
; 설치 완료 후 프로그램 실행 옵션
Filename: "{app}\{#AppExeName}"; Description: "GUARDIAN 실행"; Flags: nowait postinstall skipifsilent

[Code]
// Windows Defender 예외 등록 (설치 경로)
procedure AddDefenderExclusion();
var
  ResultCode: Integer;
begin
  // 설치 폴더를 Defender 예외에 추가
  Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -Command "' +
    'try { ' +
    '  Add-MpPreference -ExclusionPath ''' + ExpandConstant('{app}') + ''' -ErrorAction SilentlyContinue; ' +
    '  Add-MpPreference -ExclusionProcess ''' + ExpandConstant('{app}\{#AppExeName}') + ''' -ErrorAction SilentlyContinue; ' +
    '} catch { }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Windows Defender 예외 제거 (언인스톨 시)
procedure RemoveDefenderExclusion();
var
  ResultCode: Integer;
begin
  Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -Command "' +
    'try { ' +
    '  Remove-MpPreference -ExclusionPath ''' + ExpandConstant('{app}') + ''' -ErrorAction SilentlyContinue; ' +
    '  Remove-MpPreference -ExclusionProcess ''' + ExpandConstant('{app}\{#AppExeName}') + ''' -ErrorAction SilentlyContinue; ' +
    '} catch { }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    AddDefenderExclusion();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveDefenderExclusion();
  end;
end;
