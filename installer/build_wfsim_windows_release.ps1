# Build standalone Windows WFSim-linked edition packages:
#   1. dist_wfsim\裂罅透镜_WFSim联动版\裂罅透镜_WFSim联动版.exe
#   2. release\裂罅透镜_WFSim联动版_安装包.exe (7-Zip LZMA2 GUI self-extracting installer)
#   3. release\裂罅透镜_WFSim联动版_绿色版.zip

$ErrorActionPreference = "Stop"

$Root = "d:\Antigravity"
$PyInstaller = Join-Path $Root ".venv_build\Scripts\pyinstaller.exe"
$DistDir = Join-Path $Root "dist_wfsim"
$AppDir = Join-Path $DistDir "裂罅透镜_WFSim联动版"
$StageRoot = Join-Path $Root "build\_wfsim_release_stage"
$StageApp = Join-Path $StageRoot "裂罅透镜_WFSim联动版"
$ReleaseDir = Join-Path $Root "release"

if (-not (Test-Path $PyInstaller)) {
    throw "Missing PyInstaller at $PyInstaller"
}

Write-Output "[1/4] Compiling RivenOverlay_wfsim.spec via PyInstaller..."
Push-Location $Root
try {
    & $PyInstaller "RivenOverlay_wfsim.spec" --clean -y --distpath $DistDir
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageApp | Out-Null

Write-Output "[2/4] Staging clean WFSim-edition distribution copy..."
Copy-Item -Path (Join-Path $AppDir "*") -Destination $StageApp -Recurse -Force

foreach ($relDrop in @(
    "裂罅透镜.log",
    "裂罅透镜_WFSim联动版.log",
    "_internal\data\user_settings.json",
    "_internal\data\roi_profiles.json",
    "_internal\data\ocr_models"
)) {
    $p = Join-Path $StageApp $relDrop
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

$adminBat = @'
@echo off
chcp 65001 >nul
setlocal
set "APP_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = $env:APP_DIR.TrimEnd('\'); Start-Process -FilePath (Join-Path $d '裂罅透镜_WFSim联动版.exe') -WorkingDirectory $d -Verb RunAs"
'@
[System.IO.File]::WriteAllText((Join-Path $StageApp "启动WFSim联动版_管理员.bat"), $adminBat, [System.Text.UTF8Encoding]::new($false))

$readmeTxt = @'
裂罅透镜 · WFSim 联动版（Windows）使用说明
================================================

本版本在标准版全部功能基础上，新增「本地紫卡配装优化」与「一键跳转 WFSim (https://wfsim.app) 战斗模拟」：

1. 识别任意一张紫卡（单卡自动 / 洗卡双卡 / 聊天 F9 抓取）后，按 Ctrl+Alt+D 打开设置面板（打开面板或切换出游戏时，工作模式会自动切回待命，避免后台误识别）：
   - 点击「🧠 本地紫卡配装优化」：围绕当前紫卡自动避开稀释，秒算最优 7 张 Mod + 赋能组合，也可一键把优化后的整套配装发送到 WFSim 验证。
   - 点击「🚀 发送紫卡到 WFSim 模拟」：直接带入紫卡精确数值与基础钢铁之路配卡跳转到 WFSim（仅通过面板按钮发送，无全局快捷键）。
2. 浏览器会自动打开 https://wfsim.app 对应武器页面，并已自动帮你填好：
   - 当前扫描到的精确紫卡词条、正负结构与数值 Roll 系数；
   - 对应武器类型的钢铁之路配卡（7 张主流/优化 Mod + 赋能 + 第 8 格自动装备你的紫卡）。
   - 扎里曼灵化非暴吞噬流（奏凯/凤殁/逐枭）还会自动切换为非暴基础配卡。
3. 本版本完全通过标准浏览器链接跳转 WFSim 官网，未修改任何游戏文件、零内存读取。
'@
[System.IO.File]::WriteAllText((Join-Path $StageApp "WFSim联动版说明.txt"), $readmeTxt, [System.Text.UTF8Encoding]::new($true))

$sevenZip = "C:\Program Files\7-Zip\7z.exe"
$sfxMod = "C:\Program Files\7-Zip\7z.sfx"
$sfxOut = Join-Path $ReleaseDir "裂罅透镜_WFSim联动版_安装包.exe"
$zipOut = Join-Path $ReleaseDir "裂罅透镜_WFSim联动版_绿色版.zip"
$tmp7z = Join-Path $StageRoot "payload.7z"

if (Test-Path $sfxOut) { Remove-Item $sfxOut -Force }
if (Test-Path $zipOut) { Remove-Item $zipOut -Force }

if ((Test-Path $sevenZip) -and (Test-Path $sfxMod)) {
    Write-Output "[3/4] Building LZMA2 7-Zip GUI Self-Extracting Installer (.exe)..."
    Push-Location $StageRoot
    try {
        & $sevenZip a -t7z -m0=lzma2 -mx=7 -mmt=on -ms=on $tmp7z "裂罅透镜_WFSim联动版" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7z compression failed ($LASTEXITCODE)" }
        $outFs = [System.IO.File]::Create($sfxOut)
        try {
            $bSfx = [System.IO.File]::ReadAllBytes($sfxMod)
            $outFs.Write($bSfx, 0, $bSfx.Length)
            $inFs = [System.IO.File]::OpenRead($tmp7z)
            try { $inFs.CopyTo($outFs) } finally { $inFs.Close() }
        } finally {
            $outFs.Close()
        }
        Remove-Item $tmp7z -Force -ErrorAction SilentlyContinue

        Write-Output "[4/4] Building Portable ZIP archive..."
        & $sevenZip a -tzip -mx=5 -mmt=on $zipOut "裂罅透镜_WFSim联动版" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7z zip build failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
} else {
    Compress-Archive -Path $StageApp -DestinationPath $zipOut -Force
}

Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Output "=== WFSim Edition Packages Ready ==="
Get-ChildItem $ReleaseDir -Filter "裂罅透镜_WFSim联动版_*" | Select-Object FullName, @{N="SizeMB";E={[math]::Round($_.Length/1MB, 1)}}, LastWriteTime
