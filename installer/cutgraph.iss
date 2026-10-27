#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

[Setup]
AppId={{A0352AC4-4E53-4582-84D1-B1D719AA4FD4}
AppName=Cutgraph Studio
AppVersion={#AppVersion}
AppVerName=Cutgraph Studio {#AppVersion} (unsigned)
VersionInfoDescription=Cutgraph Studio Setup (unsigned)
VersionInfoVersion={#WindowsVersion}
DefaultDirName={localappdata}\Programs\Cutgraph Studio
DefaultGroupName=Cutgraph Studio
PrivilegesRequired=lowest
#if VER >= EncodeVer(6, 3, 0)
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
#else
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#endif
MinVersion=10.0.22000
DisableProgramGroupPage=yes
DisableDirPage=yes
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\Cutgraph Studio.exe
SetupIconFile={#SourcePath}\..\ui\public\assets\cutgraph.ico
LicenseFile={#BundleDir}\LICENSE
InfoBeforeFile={#BundleDir}\THIRD-PARTY-NOTICES.txt
OutputDir={#OutputDir}
OutputBaseFilename=CutgraphStudio-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
Uninstallable=yes
; Future signing: configure SignTool and SignedUninstaller together, sign the launcher
; before inventorying the payload, then sign Setup. Never sign the FFmpeg executables.

[Messages]
ConfirmUninstall=Remove %1 with the selected options?%n%nWorkspaces, Videos, media and keys will stay in your data folder.

[Tasks]
Name: desktopicon; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "{#BundleDir}\Cutgraph Studio.exe"; DestName: "path-check.exe"; Flags: dontcopy
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
Filename: "{app}\.cutgraph-install"; Section: "Install"; Key: "Id"; String: "A0352AC4-4E53-4582-84D1-B1D719AA4FD4"; Flags: uninsdeleteentry uninsdeletesectionifempty

[UninstallDelete]
Type: files; Name: "{app}\.cutgraph-install"

[Icons]
Name: "{autoprograms}\Cutgraph Studio"; Filename: "{app}\Cutgraph Studio.exe"; AppUserModelID: "Cutgraph.Studio"
Name: "{autodesktop}\Cutgraph Studio"; Filename: "{app}\Cutgraph Studio.exe"; Tasks: desktopicon; AppUserModelID: "Cutgraph.Studio"

[Run]
Filename: "{app}\Cutgraph Studio.exe"; Description: "Launch Cutgraph Studio"; Flags: postinstall nowait skipifsilent
Filename: "{app}\Cutgraph Studio.exe"; Flags: nowait; Check: IsUpdate

[Code]
const
  InstallId = 'A0352AC4-4E53-4582-84D1-B1D719AA4FD4';
var
  RemoveAddons: Boolean;
  UninstallOwned: Boolean;
  PathHelperExe: String;

function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:CUTGRAPHUPDATE|0}') = '1';
end;

function DirectoryEmpty(const Directory: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Directory) + '*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          Result := False;
          exit;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

function PathHelper: String;
begin
  if PathHelperExe = '' then begin
    PathHelperExe := ExpandConstant('{tmp}\path-check.exe');
    if IsUninstaller then begin
      if not FileCopy(ExpandConstant('{app}\Cutgraph Studio.exe'), PathHelperExe, False) then
        RaiseException('The application safety helper could not be prepared.');
    end
    else ExtractTemporaryFile('path-check.exe');
  end;
  Result := PathHelperExe;
end;

function DestinationError: String;
var
  ExitCode: Integer;
begin
  Result := '';
  if not Exec(PathHelper, '--check-install-path ' + AddQuotes(ExpandConstant('{app}')),
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then ExitCode := 1;
  case ExitCode of
    0: Result := '';
    2: Result := 'CUTGRAPH_STATE_HOME must be an absolute data-folder path. Change or unset this environment variable, then try again.';
    3: Result := 'The application folder and Studio data folder overlap. Select a separate data folder with CUTGRAPH_STATE_HOME, then try again.';
    4: Result := 'The application folder contains a symbolic link, junction or mount point. Remove the link from the application folder, then try again. Linked parent folders are supported.';
  else
    Result := 'The application folder could not be checked. Close Studio and other programs using this folder, check its permissions, then try again.';
  end;
end;

function OwnedDirectory: Boolean;
begin
  Result := GetIniString('Install', 'Id', '', ExpandConstant('{app}\.cutgraph-install')) = InstallId;
end;

function StopStudio: Boolean;
var
  ExitCode: Integer;
  Launcher: String;
begin
  Launcher := ExpandConstant('{app}\Cutgraph Studio.exe');
  Result := not FileExists(Launcher);
  if not Result then
    Result := Exec(Launcher, '--stop', ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
end;

function ClearProgramFiles: Boolean;
var
  Attempt, ExitCode: Integer;
begin
  if not OwnedDirectory then begin
    Result := not DirExists(ExpandConstant('{app}')) or DirectoryEmpty(ExpandConstant('{app}'));
    exit;
  end;
  Result := False;
  for Attempt := 1 to 100 do begin
    Result := Exec(PathHelper, '--clear-program-files ' + AddQuotes(ExpandConstant('{app}')),
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
    if Result then exit;
    Sleep(100);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := DestinationError;
  if Result <> '' then exit;
  if DirExists(ExpandConstant('{app}')) and not OwnedDirectory and
     not DirectoryEmpty(ExpandConstant('{app}')) then begin
    Result := 'The application folder contains other files and cannot be replaced. Move those files before installing.';
    exit;
  end;
  if not StopStudio then begin
    Result := 'Studio could not stop. Close Studio and try the upgrade again.';
    exit;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Error: String;
begin
  if CurStep = ssInstall then begin
    Error := DestinationError;
    if Error <> '' then RaiseException(Error);
    if (DirExists(ExpandConstant('{app}')) and not OwnedDirectory and
        not DirectoryEmpty(ExpandConstant('{app}'))) or not ClearProgramFiles then
      RaiseException('The previous application files could not be removed. Close Studio and try again.');
  end;
end;

function InitializeUninstall: Boolean;
var
  Dialog: TSetupForm;
  Choice: TNewCheckBox;
  Explanation: TNewStaticText;
  ContinueButton, CancelButton: TNewButton;
  Error: String;
begin
  Result := False;
  Error := DestinationError;
  if Error <> '' then begin
    SuppressibleMsgBox(Error, mbError, MB_OK, IDOK);
    exit;
  end;
  UninstallOwned := OwnedDirectory;
  if not UninstallOwned then begin
    SuppressibleMsgBox('The installation record is missing or damaged. Restore it from a backup before uninstalling. Your Studio data has been kept.', mbError, MB_OK, IDOK);
    exit;
  end;
  RemoveAddons := ExpandConstant('{param:REMOVEADDONS|0}') = '1';
  if not UninstallSilent then begin
#if VER >= EncodeVer(6, 6, 0)
    Dialog := CreateCustomForm(ScaleX(470), ScaleY(155), True, True);
#else
    Dialog := CreateCustomForm;
    Dialog.ClientWidth := ScaleX(470);
    Dialog.ClientHeight := ScaleY(155);
#endif
    try
      Dialog.Caption := 'Cutgraph Studio uninstall options';
      Choice := TNewCheckBox.Create(Dialog);
      Choice.Parent := Dialog;
      Choice.SetBounds(ScaleX(16), ScaleY(20), ScaleX(438), ScaleY(25));
      Choice.Caption := 'Also remove downloaded add-ons';
      Choice.Checked := False;
      Explanation := TNewStaticText.Create(Dialog);
      Explanation.Parent := Dialog;
      Explanation.SetBounds(ScaleX(16), ScaleY(55), ScaleX(438), ScaleY(40));
      Explanation.AutoSize := False;
      Explanation.WordWrap := True;
      Explanation.Caption := 'Workspaces, Videos, media and keys are kept in your data folder.';
      ContinueButton := TNewButton.Create(Dialog);
      ContinueButton.Parent := Dialog;
      ContinueButton.SetBounds(ScaleX(270), ScaleY(110), ScaleX(85), ScaleY(25));
      ContinueButton.Caption := 'Next'; ContinueButton.ModalResult := mrOk; ContinueButton.Default := True;
      CancelButton := TNewButton.Create(Dialog);
      CancelButton.Parent := Dialog;
      CancelButton.SetBounds(ScaleX(365), ScaleY(110), ScaleX(85), ScaleY(25));
      CancelButton.Caption := 'Cancel'; CancelButton.ModalResult := mrCancel; CancelButton.Cancel := True;
      if Dialog.ShowModal <> mrOk then exit;
      RemoveAddons := Choice.Checked;
    finally
      Dialog.Free;
    end;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExitCode: Integer;
  Error: String;
begin
  if CurUninstallStep = usUninstall then begin
    Error := DestinationError;
    if Error <> '' then RaiseException(Error);
    if not OwnedDirectory then
      RaiseException('The application folder cannot be safely removed.');
    if not StopStudio then RaiseException('Studio could not stop. Close Studio and try uninstalling again.');
    if RemoveAddons then begin
      if not Exec(ExpandConstant('{app}\Cutgraph Studio.exe'), '--remove-addons',
        ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
        RaiseException('Downloaded add-ons could not be safely removed. Studio data has been kept.');
    end;
    if not ClearProgramFiles then RaiseException('Application files are still in use. Close Studio and try again.');
  end;
end;
