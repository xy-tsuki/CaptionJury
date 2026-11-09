; Inno Setup 安装包脚本（需要 Inno Setup 6.5 以上）。由 packaging/build.ps1 调用：
;   ISCC.exe /DAppVersion=0.1.0 /DSrc=<dist 目录> /DOut=<输出目录> packaging\installer.iss
; 安装时让用户选"只为我安装"（默认，不需要管理员）或"为所有用户安装"。
; 程序本身装在安装目录；模型、运行库、设置和任务都在 %LOCALAPPDATA%\Tingxie，卸载时询问是否删除。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Src
  #define Src "..\build\dist"
#endif
#ifndef Out
  #define Out "..\build\installer"
#endif

[Setup]
AppId={{6B7B2C1E-3F0A-4D2B-9C5E-2A8F1D4E7B90}
AppName=听写
AppVersion={#AppVersion}
AppVerName=听写 {#AppVersion}
AppPublisher=星月Tsuki
AppPublisherURL=https://github.com/xy-tsuki/tingxie
AppSupportURL=https://github.com/xy-tsuki/tingxie/issues
DefaultDirName={autopf}\Tingxie
DefaultGroupName=听写
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Out}
OutputBaseFilename=Tingxie-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Tingxie.exe
UninstallDisplayName=听写 {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
InfoBeforeFile=install-info.txt

[Languages]
Name: "zh"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[InstallDelete]
; 升级时先清掉旧版本的依赖文件，免得残留
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; build\dist\Tingxie 的结构和安装后一样：Tingxie.exe、_internal、ffmpeg、licenses
Source: "{#Src}\Tingxie\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Src}\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedWebView2

[Icons]
Name: "{autoprograms}\听写"; Filename: "{app}\Tingxie.exe"
Name: "{autodesktop}\听写"; Filename: "{app}\Tingxie.exe"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装 Microsoft Edge WebView2 运行时……"; Check: NeedWebView2; Flags: waituntilterminated
Filename: "{app}\Tingxie.exe"; Description: "启动听写"; Flags: nowait postinstall skipifsilent

[Code]
const
  WV2 = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

{ 程序界面用 WebView2 显示。Windows 11 和大多数 Windows 10 已经自带，没有的话安装时顺便装上。 }
function HasWebView2(Root: Integer; Key: String): Boolean;
var
  V: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0');
end;

function NeedWebView2: Boolean;
begin
  Result := not (HasWebView2(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WV2) or
                 HasWebView2(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WV2) or
                 HasWebView2(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WV2));
end;

{ 卸载时问要不要连下载的模型和设置一起删。模型可能有十几 GB，默认保留，方便重装后直接用。 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\Tingxie');
    if DirExists(Data) and not UninstallSilent then
      if MsgBox('是否同时删除下载的模型、运行库、设置和转写记录？' #13#10#13#10 + Data + #13#10#13#10 +
                '选"否"会保留这些文件，重新安装后可以直接使用。', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
