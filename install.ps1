#Requires -Version 5.1
param(
    [Alias("e")]
    [string]$Editable = ""
)
# install.ps1 — CorvinOS installer for Windows (PowerShell).
# Usage:
#   irm https://corvin-labs.com/install.ps1 | iex
#   .\install.ps1 -Editable C:\path\to\CorvinOS   # dev install from local clone
#   .\install.ps1 -e C:\path\to\CorvinOS

$ErrorActionPreference = "Stop"
$VenvDir   = Join-Path $env:USERPROFILE "corvin_venv"
$Package   = "corvinos"

function Write-Step  { param($msg) Write-Host "  $msg" }
function Write-Ok    { param($msg) Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "`n  Error: $msg" -ForegroundColor Red; exit 1 }
function Write-Head  { param($msg) Write-Host $msg -ForegroundColor Cyan }
function Write-Cmd   { param($msg) Write-Host "    $msg" -ForegroundColor White }
function Write-Hint  { param($msg) Write-Host "    $msg" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "CorvinOS installer" -ForegroundColor White

# ── editable path validation ──────────────────────────────────────────────────

$EditablePath = ""
if ($Editable -ne "") {
    if (-not (Test-Path $Editable -PathType Container)) {
        Write-Fail "Editable path does not exist: $Editable"
    }
    $EditablePath = (Resolve-Path $Editable).Path
}

# ── Python version check ──────────────────────────────────────────────────────

$Python = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -match "^3\.(\d+)$") {
            $minor = [int]$Matches[1]
            if ($minor -ge 10) {
                $Python = $candidate
                break
            }
        }
    } catch {}
}

if (-not $Python) {
    Write-Fail @"
Python 3.10+ is required but not found.
  Download: https://www.python.org/downloads/windows/
  Tip: check 'Add Python to PATH' during installation.
"@
}

$pyVer = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Ok "Python $pyVer — OK"

# ── virtual environment ───────────────────────────────────────────────────────

Write-Step "Creating virtual environment at $VenvDir ..."
& $Python -m venv $VenvDir
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Failed to create virtual environment at $VenvDir"
}

$Pip       = Join-Path $VenvDir "Scripts\pip.exe"
$CorvinBin = Join-Path $VenvDir "Scripts\corvinos-serve.exe"

# ── install package ───────────────────────────────────────────────────────────

Write-Step "Upgrading pip ..."
& $Pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to upgrade pip" }

if ($EditablePath -ne "") {
    Write-Step "Installing CorvinOS in editable mode from $EditablePath ..."
    & $Pip install -e $EditablePath
} else {
    Write-Step "Installing $Package ..."
    & $Pip install $Package
}

if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install failed — see the error above"
}

if (-not (Test-Path $CorvinBin)) {
    Write-Fail "Installation succeeded but 'corvinos-serve.exe' not found at $CorvinBin"
}

# ── PATH setup (User scope, permanent) ───────────────────────────────────────

$BinDir   = Join-Path $VenvDir "Scripts"
$UserPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")

if ($UserPath -notlike "*$BinDir*") {
    [System.Environment]::SetEnvironmentVariable(
        "PATH",
        "$BinDir;$UserPath",
        "User"
    )
    Write-Ok "Added $BinDir to your user PATH."
} else {
    Write-Step "$BinDir is already in PATH."
}

# ── run setup wizard ──────────────────────────────────────────────────────────

$CorvinInstallBin = Join-Path $VenvDir "Scripts\corvin-install.exe"

Write-Host ""
Write-Ok "Package installed."

if (Test-Path $CorvinInstallBin) {
    Write-Host ""
    Write-Step "Launching setup wizard ..."
    Write-Host ""
    & $CorvinInstallBin
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Setup wizard exited with an error. You can re-run it later with: corvin-install"
    }
}

# ── done / cheat sheet ───────────────────────────────────────────────────────

Write-Host ""
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host " CorvinOS is ready!" -ForegroundColor Green
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""
Write-Host " Step 1 — Open a new terminal window " -ForegroundColor White -NoNewline
Write-Host "(so PATH is updated)" -ForegroundColor DarkGray
Write-Host "         Or activate right now without restarting:" -ForegroundColor DarkGray
Write-Host ""
Write-Cmd  "$VenvDir\Scripts\Activate.ps1"
Write-Hint "# Type 'deactivate' to leave the environment again"
Write-Host ""
Write-Host " Step 2 — Start the web console" -ForegroundColor White
Write-Host ""
Write-Cmd  "corvinos-serve"
Write-Hint "# Then open:  http://localhost:8765/console/"
Write-Host ""
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host " All available commands" -ForegroundColor White
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""
Write-Host "   corvinos-serve     " -NoNewline -ForegroundColor White; Write-Host "Start the web console"
Write-Host "   corvin-install     " -NoNewline -ForegroundColor White; Write-Host "Run the setup wizard (bridges, tokens, voice)"
Write-Host "   corvin-uninstall   " -NoNewline -ForegroundColor White; Write-Host "Remove CorvinOS (services, plugins, config)"
Write-Host "   corvin-restore     " -NoNewline -ForegroundColor White; Write-Host "Restore a previous installation"
Write-Host "   corvin-flow        " -NoNewline -ForegroundColor White; Write-Host "Manage declarative multi-node workflows"
Write-Host "   corvin-layer       " -NoNewline -ForegroundColor White; Write-Host "Manage layer extensions"
Write-Host "   corvin-a2a         " -NoNewline -ForegroundColor White; Write-Host "Agent-to-agent pairing and messaging"
Write-Host ""
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host " Optional: local AI model" -ForegroundColor White -NoNewline
Write-Host "  (for offline / private use)" -ForegroundColor DarkGray
Write-Head "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""
Write-Cmd  "ollama pull qwen3:8b     # 5.2 GB  — enables /engine hermes"
Write-Cmd  "ollama pull qwen3:1.7b   # 1.4 GB  — lighter/faster variant"
Write-Hint "Skip if you only use cloud engines (Claude, Codex, Copilot)."
Write-Host ""
