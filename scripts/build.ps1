$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $project
try {
    $icon = (Resolve-Path -LiteralPath 'src/scanmap/assets/ScanMap.ico').Path
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name WoWScanMap `
        --icon $icon --add-data "$icon;assets" `
        --workpath build/work --specpath build --distpath dist src/scanmap/minimap_alert.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
