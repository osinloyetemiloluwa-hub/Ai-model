#Requires -Version 5.1
# bridge.ps1 — CorvinOS bridge launcher for Windows (ADR-0159 M4).
#
# Equivalent to bridge.sh on Linux/macOS. Activates the venv and dispatches
# subcommands: up, down, doctor, restart, status, console.
#
# Usage:
#   .\bridge.ps1 up             # Start Discord (default) bridge
#   .\bridge.ps1 up whatsapp    # Start WhatsApp bridge
#   .\bridge.ps1 doctor         # Run self-test
#   .\bridge.ps1 status         # Show running bridge processes
#   .\bridge.ps1 down           # Stop bridge(s)
#   .\bridge.ps1 restart        # Restart bridge(s)
#   .\bridge.ps1 console        # Start web console (background)
#
# Prerequisites (set up by install.ps1):
#   - Python 3.11+ in PATH or %USERPROFILE%\.corvinos\Scripts\python.exe
#   - CorvinOS installed via: pip install -e .
#   - Ollama running (for hermes engine): ollama serve
#   - (Optional) claude CLI for claude_code engine
#
# Environment:
#   CORVIN_HOME         Override corvin home dir (default: %USERPROFILE%\.corvin)
#   CORVIN_TENANT_ID    Override tenant (default: _default)
#   CORVIN_OS_ENGINE    Override engine detection (hermes|claude_code|opencode)
#   CORVIN_SANDBOX      Override sandbox tier (bwrap|docker|none)
#   CORVIN_BRIDGE_PORT  Set when TCP loopback transport is active (auto-set)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Python / venv resolution ──────────────────────────────────────────────────

function Find-Python {
    $venvPy = Join-Path $env:USERPROFILE ".corvinos\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $ver = & $cmd -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>$null
            if ($ver -match "^3\.(\d+)" -and [int]$Matches[1] -ge 11) {
                return $cmd
            }
        } catch {}
    }
    Write-Error "Python 3.11+ not found. Run install.ps1 first."
    exit 1
}

$Python = Find-Python

# ── Corvin home ───────────────────────────────────────────────────────────────

if (-not $env:CORVIN_HOME) {
    $env:CORVIN_HOME = Join-Path $env:USERPROFILE ".corvin"
}

# ── Subcommand dispatch ───────────────────────────────────────────────────────

$Subcommand = if ($args.Count -gt 0) { $args[0] } else { "up" }
$Bridge     = if ($args.Count -gt 1) { $args[1] } else { "discord" }

switch ($Subcommand) {

    "up" {
        Write-Host "Starting $Bridge bridge (Windows / TCP-loopback transport)..." -ForegroundColor Cyan

        # Allocate a free TCP port for the bridge sidecar transport (ADR-0159 M4).
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $listener.Start()
        $BridgePort = $listener.LocalEndpoint.Port
        $listener.Stop()
        $env:CORVIN_BRIDGE_PORT = "$BridgePort"
        Write-Host "  Bridge TCP loopback port: $BridgePort" -ForegroundColor DarkGray

        # Write port to corvin home for discovery by other processes.
        $PortFile = Join-Path $env:CORVIN_HOME "global\bridge.port"
        $null = New-Item -ItemType Directory -Force -Path (Split-Path $PortFile)
        Set-Content -Path $PortFile -Value "$BridgePort" -Encoding ASCII -NoNewline
        icacls $PortFile /inheritance:r /grant:r "${env:USERNAME}:(R,W)" | Out-Null

        # Launch the bridge adapter
        $BridgeScript = Join-Path $ScriptDir "$Bridge\daemon.js"
        if (Test-Path $BridgeScript) {
            Write-Host "  Launching Node.js daemon: $BridgeScript" -ForegroundColor DarkGray
            & node $BridgeScript
        } else {
            Write-Error "Bridge script not found: $BridgeScript"
            exit 1
        }
    }

    "doctor" {
        Write-Host "Running CorvinOS self-test..." -ForegroundColor Cyan
        & $Python -c "
import sys
sys.path.insert(0, r'$ScriptDir\shared')
from self_test import run_self_test, CRITICAL, WARNING
result = run_self_test()
for c in result.checks:
    icon = 'OK' if c.ok else c.severity
    print(f'  [{icon}] {c.name}: {c.detail}')
failed_crit = [c for c in result.checks if not c.ok and c.severity == CRITICAL]
if failed_crit:
    print(f'\nFAILED: {len(failed_crit)} CRITICAL check(s)')
    sys.exit(1)
else:
    print('\nAll CRITICAL checks passed.')
"
    }

    "status" {
        Write-Host "CorvinOS bridge processes:" -ForegroundColor Cyan
        Get-Process | Where-Object {
            $_.ProcessName -match "node" -or $_.ProcessName -match "python"
        } | Select-Object ProcessName, Id, CPU, WorkingSet | Format-Table -AutoSize
    }

    "down" {
        Write-Host "Stopping CorvinOS bridge processes..." -ForegroundColor Cyan
        Get-Process | Where-Object { $_.ProcessName -match "node" } | ForEach-Object {
            try { Stop-Process -Id $_.Id -Force; Write-Host "  Stopped: $($_.Id)" } catch {}
        }
        # Remove bridge port file
        $PortFile = Join-Path $env:CORVIN_HOME "global\bridge.port"
        if (Test-Path $PortFile) { Remove-Item $PortFile -Force }
    }

    "restart" {
        & $MyInvocation.MyCommand.Path down
        Start-Sleep -Milliseconds 500
        & $MyInvocation.MyCommand.Path up $Bridge
    }

    "console" {
        Write-Host "Starting CorvinOS web console..." -ForegroundColor Cyan
        $ConsoleScript = Join-Path $ScriptDir "shared\corvin_gateway.py"
        Start-Process -FilePath $Python -ArgumentList $ConsoleScript -WindowStyle Hidden
        Write-Host "  Console started in background. Check http://localhost:8765" -ForegroundColor Green
    }

    default {
        Write-Host "Usage: .\bridge.ps1 <up|down|restart|doctor|status|console> [bridge]" -ForegroundColor Yellow
        exit 1
    }
}
