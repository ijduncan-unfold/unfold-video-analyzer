#ifndef AppVersion
  #define AppVersion "0.1.0-preview"
#endif
#ifndef BundleDir
  #define BundleDir "..\dist\desktop\VideoAnalyzer"
#endif
#ifndef PremierePackage
  #define PremierePackage "..\dist\installers\VideoAnalyzer-Premiere-0.1.11.ccx"
#endif

[Setup]
AppId={{E9AEC97E-9D51-4FBE-8B29-0C4C0EDAE140}
AppName=Video Analyzer
AppVersion={#AppVersion}
AppPublisher=IJDuncan
AppPublisherURL=https://github.com/ijduncan/video-analyzer
DefaultDirName={localappdata}\Programs\Video Analyzer
DefaultGroupName=Video Analyzer
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installers
OutputBaseFilename=VideoAnalyzer-{#AppVersion}-windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\VideoAnalyzer.exe
CloseApplications=yes
RestartApplications=no
InfoBeforeFile=windows-setup.txt

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: installffmpeg; Description: "Install FFmpeg via WinGet (required for video; GPLv3, third-party package)"; GroupDescription: "Media tools:"; Check: NeedsFFmpeg

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PremierePackage}"; DestDir: "{app}\plugins"; Flags: ignoreversion

[Icons]
Name: "{group}\Video Analyzer"; Filename: "{app}\VideoAnalyzer.exe"
Name: "{group}\Pair Video Analyzer with editor"; Filename: "{app}\VideoAnalyzer.exe"; Parameters: "--pair-editor"
Name: "{group}\Install Premiere panel"; Filename: "{app}\plugins\VideoAnalyzer-Premiere-0.1.11.ccx"
Name: "{group}\Install Resolve integration"; Filename: "{app}\VideoAnalyzer.exe"; Parameters: "--install-resolve-panel"
Name: "{group}\Uninstall Video Analyzer"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Video Analyzer"; Filename: "{app}\VideoAnalyzer.exe"; Tasks: desktopicon

[Run]
Filename: "{code:WinGetPath}"; Parameters: "install --id Gyan.FFmpeg --exact --source winget --silent --disable-interactivity --accept-source-agreements --accept-package-agreements"; StatusMsg: "Installing FFmpeg using Windows Package Manager..."; Flags: runhidden; Tasks: installffmpeg; Check: HasWinGet; AfterInstall: VerifyFFmpeg
Filename: "{app}\VideoAnalyzer.exe"; Description: "Open Video Analyzer"; Flags: nowait postinstall skipifsilent

[Code]
function WinGetPath(Param: String): String;
begin
  Result := ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe');
end;

function HasWinGet: Boolean;
begin
  Result := FileExists(WinGetPath(''));
end;

function NeedsFFmpeg: Boolean;
var
  PackageRec, ToolRec: TFindRec;
  PackageRoot, ToolRoot, PathDirs, DirName: String;
  Separator: Integer;
begin
  Result := True;
  PathDirs := GetEnv('PATH') + ';';
  while PathDirs <> '' do
  begin
    Separator := Pos(';', PathDirs);
    DirName := Copy(PathDirs, 1, Separator - 1);
    Delete(PathDirs, 1, Separator);
    if FileExists(AddBackslash(DirName) + 'ffmpeg.exe') and
       FileExists(AddBackslash(DirName) + 'ffprobe.exe') then
    begin
      Result := False;
      exit;
    end;
  end;
  if FileExists(ExpandConstant('{localappdata}\Microsoft\WinGet\Links\ffmpeg.exe')) and
     FileExists(ExpandConstant('{localappdata}\Microsoft\WinGet\Links\ffprobe.exe')) then
  begin
    Result := False;
    exit;
  end;
  PackageRoot := ExpandConstant('{localappdata}\Microsoft\WinGet\Packages\');
  if FindFirst(PackageRoot + 'Gyan.FFmpeg_*', PackageRec) then
  begin
    repeat
      if (PackageRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        ToolRoot := PackageRoot + PackageRec.Name + '\';
        if FindFirst(ToolRoot + 'ffmpeg*', ToolRec) then
        begin
          repeat
            if FileExists(ToolRoot + ToolRec.Name + '\bin\ffmpeg.exe') and
               FileExists(ToolRoot + ToolRec.Name + '\bin\ffprobe.exe') then Result := False;
          until not FindNext(ToolRec);
          FindClose(ToolRec);
        end;
      end;
    until (not Result) or not FindNext(PackageRec);
    FindClose(PackageRec);
  end;
end;

procedure VerifyFFmpeg;
begin
  if NeedsFFmpeg then
    MsgBox('FFmpeg installation did not finish. Check your network connection and install Gyan.FFmpeg through Windows Package Manager, then reopen Video Analyzer. The application itself was installed successfully.', mbInformation, MB_OK);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectTasks) and WizardIsTaskSelected('installffmpeg') and not HasWinGet then
    MsgBox('Windows Package Manager was not found. Install App Installer from Microsoft, then install FFmpeg. Video Analyzer can still be installed now; video imports need FFmpeg before they can run.', mbInformation, MB_OK);
end;

// Deliberately no UninstallDelete: saved analysis, pairing token, imported media,
// and original source files are never removed by uninstalling this application.
