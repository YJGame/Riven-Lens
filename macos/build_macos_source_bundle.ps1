# Package a clean, zero-personal-data macOS source bundle for building RivenLens.app & .dmg on any Mac.
# Outputs:
#   - release\RivenLens_macOS_Source.tar.gz
#   - release\RivenLens_macOS_Source.zip

$ErrorActionPreference = "Stop"

$Root = "d:\Antigravity"
$StageRoot = Join-Path $Root "build\_macos_source_stage"
$StageDir = Join-Path $StageRoot "RivenLens_macOS_Source"
$ReleaseDir = Join-Path $Root "release"

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

$copyItems = @(
    "main.py",
    "README.md",
    "requirements-macos.txt",
    "RivenOverlay_macos.spec",
    "src",
    "locales",
    "assets",
    "tests",
    "macos"
)

foreach ($item in $copyItems) {
    $srcPath = Join-Path $Root $item
    if (Test-Path $srcPath) {
        Copy-Item -Path $srcPath -Destination (Join-Path $StageDir $item) -Recurse -Force
    }
}

# Copy clean baseline data JSONs only (never copy personal user_settings.json / ocr_models)
$dataDst = Join-Path $StageDir "data"
New-Item -ItemType Directory -Force -Path $dataDst | Out-Null
foreach ($jsonName in @(
    "custom_riven_stats.json",
    "prime_prices.json",
    "recommended_tiers.json",
    "riven_attributes.json",
    "roi_profiles.json",
    "weapons.json",
    "weapons_riven.json"
)) {
    $jp = Join-Path (Join-Path $Root "data") $jsonName
    if (Test-Path $jp) {
        Copy-Item -Path $jp -Destination (Join-Path $dataDst $jsonName) -Force
    }
}

# Strip any __pycache__ / *.pyc / *.log inside staging tree
Get-ChildItem -Path $StageDir -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Get-ChildItem -Path $StageDir -File -Include "*.pyc","*.pyo","*.log" -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force

# Ensure shell scripts use LF line endings for macOS bash!
foreach ($shFile in (Get-ChildItem -Path (Join-Path $StageDir "macos") -Filter "*.sh")) {
    $txt = [System.IO.File]::ReadAllText($shFile.FullName).Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($shFile.FullName, $txt, [System.Text.UTF8Encoding]::new($false))
}

$tgzOut = Join-Path $ReleaseDir "RivenLens_macOS_Source.tar.gz"
$zipOut = Join-Path $ReleaseDir "RivenLens_macOS_Source.zip"
if (Test-Path $tgzOut) { Remove-Item $tgzOut -Force }
if (Test-Path $zipOut) { Remove-Item $zipOut -Force }

Push-Location $StageRoot
try {
    & tar -czf $tgzOut "RivenLens_macOS_Source"
    if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

$sevenZip = "C:\Program Files\7-Zip\7z.exe"
if (Test-Path $sevenZip) {
    Push-Location $StageRoot
    try {
        & $sevenZip a -tzip -mx=7 -mmt=on $zipOut "RivenLens_macOS_Source" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7z zip failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
} else {
    Compress-Archive -Path $StageDir -DestinationPath $zipOut -Force
}

Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Output "=== macOS Source Bundles Ready ==="
Get-ChildItem $ReleaseDir -Filter "RivenLens_macOS_Source.*" | Select-Object FullName, @{N="SizeMB";E={[math]::Round($_.Length/1MB, 2)}}, LastWriteTime
