<#
构建安装包：打包 exe、准备 FFmpeg / WebView2 安装器 / 第三方许可证，再用 Inno Setup 生成安装包。
本机和 GitHub Actions 都用这个脚本。在仓库根目录运行：

    powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Python python] [-Iscc 路径] [-SkipInstaller]

输出：build\dist\Tingxie\Tingxie.exe 和 build\installer\Tingxie-Setup-<版本>.exe
下载的 FFmpeg 和 WebView2 安装器缓存在 build\cache，下次不重复下载。
#>
param(
    [string]$Python = "python",
    [string]$Iscc = "",
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest 显示进度条会慢很多
$env:PYTHONUTF8 = "1"  # CI 的控制台是 cp1252，Python 输出中文会报错
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# FFmpeg：GyanD 的 essentials 版（GPL，固定版本和 SHA-256）。程序只通过命令行调用它，属于独立程序一起分发。
$FfmpegVer = "9.0.2"
$FfmpegUrl = "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegVer/ffmpeg-$FfmpegVer-essentials_build.zip"
$FfmpegSha = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
# WebView2 常青版引导安装器，微软官方地址，不分版本；用数字签名代替 SHA-256 校验
$Wv2Url = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

$Build = Join-Path $Root "build"
$Cache = Join-Path $Build "cache"
$Dist = Join-Path $Build "dist"
New-Item -ItemType Directory -Force $Cache, $Dist | Out-Null

function Step($msg) { Write-Host "== $msg" -ForegroundColor Cyan }
function Run($exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "$exe 失败（退出码 $LASTEXITCODE）" }
}

$Version = (Select-String -Path "core\version.py" -Pattern 'VERSION = "([^"]+)"').Matches[0].Groups[1].Value
Step "听写 $Version"

Step "准备打包用的虚拟环境 build\venv"
$Venv = Join-Path $Build "venv"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Py)) { Run $Python @("-m", "venv", $Venv) }
Run $Py @("-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "packaging\requirements-build.txt")

Step "PyInstaller 打包"
Remove-Item -Recurse -Force (Join-Path $Dist "Tingxie") -ErrorAction SilentlyContinue
Run $Py @("-m", "PyInstaller", "packaging\tingxie.spec", "--noconfirm", "--distpath", $Dist, "--workpath", (Join-Path $Build "work"))

Step "FFmpeg $FfmpegVer"
$Zip = Join-Path $Cache "ffmpeg-$FfmpegVer.zip"
if (-not (Test-Path $Zip) -or (Get-FileHash $Zip -Algorithm SHA256).Hash -ne $FfmpegSha) {
    Invoke-WebRequest $FfmpegUrl -OutFile $Zip -UseBasicParsing
}
if ((Get-FileHash $Zip -Algorithm SHA256).Hash -ne $FfmpegSha) { throw "FFmpeg 压缩包 SHA-256 不对" }
$Tmp = Join-Path $Build "ffmpeg-unzip"
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
Expand-Archive $Zip $Tmp
$Top = Get-ChildItem $Tmp -Directory | Select-Object -First 1
$App = Join-Path $Dist "Tingxie"  # 和安装后的目录结构一样：ffmpeg 和 licenses 都在 exe 旁边
$Ff = New-Item -ItemType Directory -Force (Join-Path $App "ffmpeg")
Copy-Item (Join-Path $Top.FullName "bin\ffmpeg.exe"), (Join-Path $Top.FullName "bin\ffprobe.exe") $Ff

Step "第三方许可证"
$Lic = Join-Path $App "licenses"
New-Item -ItemType Directory -Force $Lic | Out-Null
Copy-Item (Join-Path $Top.FullName "LICENSE") (Join-Path $Lic "FFmpeg-LICENSE.txt")
Copy-Item (Join-Path $Top.FullName "README.txt") (Join-Path $Lic "FFmpeg-README.txt")
Run $Py @("packaging\collect_licenses.py", $Lic)
Remove-Item -Recurse -Force $Tmp

Step "WebView2 安装器"
$Wv2 = Join-Path $Cache "MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $Wv2)) { Invoke-WebRequest $Wv2Url -OutFile $Wv2 -UseBasicParsing }
$Sig = Get-AuthenticodeSignature $Wv2
if ($Sig.Status -ne "Valid" -or $Sig.SignerCertificate.Subject -notmatch "O=Microsoft Corporation") {
    Remove-Item $Wv2
    throw "WebView2 安装器的数字签名不对：$($Sig.Status) $($Sig.SignerCertificate.Subject)"
}
Copy-Item $Wv2 $Dist -Force

if ($SkipInstaller) { Step "跳过安装包。程序在 $Dist\Tingxie"; exit 0 }

Step "Inno Setup 生成安装包"
if (-not $Iscc) {
    $Iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw "找不到 Inno Setup 6 的 ISCC.exe，请安装 Inno Setup 6.5 以上，或用 -Iscc 指定路径；只要 exe 可以加 -SkipInstaller" }
Run $Iscc @("/Q", "/DAppVersion=$Version", "/DSrc=$Dist", "/DOut=$(Join-Path $Build 'installer')", "packaging\installer.iss")
Get-ChildItem (Join-Path $Build "installer") | ForEach-Object { Write-Host ("{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB)) }
