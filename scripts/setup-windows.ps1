# Daruma Windows Server Setup Script
# Run as Administrator in PowerShell
# Prerequisites: Python 3.11+, PostgreSQL 16+, PostGIS, Git must already be installed

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$INSTALL_DIR = "C:\daruma"
$REPO_URL = "https://github.com/nshelton/spatiotemporal_db.git"
$DB_NAME = "daruma"
$DB_USER = "daruma"

# --- Helpers ---

function Write-Step($msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Test-Command($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}

# --- Preflight checks ---

Write-Step "Checking prerequisites"

$missing = @()
if (-not (Test-Command "python")) { $missing += "Python (python.org/downloads)" }
if (-not (Test-Command "psql"))   { $missing += "PostgreSQL (enterprisedb.com)" }
if (-not (Test-Command "git"))    { $missing += "Git (git-scm.com)" }

if ($missing.Count -gt 0) {
    Write-Host "Missing required software:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "`nInstall these first, then re-run this script." -ForegroundColor Yellow
    exit 1
}

Write-Host "  python: $(python --version)" -ForegroundColor Green
Write-Host "  psql:   $(psql --version)" -ForegroundColor Green
Write-Host "  git:    $(git --version)" -ForegroundColor Green

# --- Prompt for passwords ---

Write-Host ""
$POSTGRES_PASSWORD = Read-Host -Prompt "Enter the password for the 'postgres' superuser" -AsSecureString
$POSTGRES_PASSWORD_PLAIN = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($POSTGRES_PASSWORD)
)

if ([string]::IsNullOrWhiteSpace($POSTGRES_PASSWORD_PLAIN)) {
    Write-Host "Password cannot be empty." -ForegroundColor Red
    exit 1
}

# Set PGPASSWORD environment variable for psql commands
$env:PGPASSWORD = $POSTGRES_PASSWORD_PLAIN

# Optionally save to .pgpass file for future runs
$savePgpass = Read-Host "Save postgres password for future runs? (y/n)"
if ($savePgpass -eq "y") {
    $pgpassDir = "$env:APPDATA\postgresql"
    $pgpassFile = "$pgpassDir\pgpass.conf"

    if (-not (Test-Path $pgpassDir)) {
        New-Item -ItemType Directory -Path $pgpassDir -Force | Out-Null
    }

    # Format: hostname:port:database:username:password
    $pgpassEntry = "localhost:5432:*:postgres:$POSTGRES_PASSWORD_PLAIN"

    # Check if entry already exists
    $existingContent = ""
    if (Test-Path $pgpassFile) {
        $existingContent = Get-Content $pgpassFile -Raw
    }

    if ($existingContent -notmatch "localhost:5432:\*:postgres:") {
        Add-Content -Path $pgpassFile -Value $pgpassEntry
        Write-Host "  Saved to $pgpassFile" -ForegroundColor Green
    } else {
        # Update existing entry
        $updatedContent = $existingContent -replace "localhost:5432:\*:postgres:.*", $pgpassEntry
        Set-Content -Path $pgpassFile -Value $updatedContent
        Write-Host "  Updated existing entry in $pgpassFile" -ForegroundColor Green
    }
}

$DB_PASSWORD = Read-Host -Prompt "Enter a password for the '$DB_USER' database user" -AsSecureString
$DB_PASSWORD_PLAIN = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($DB_PASSWORD)
)

if ([string]::IsNullOrWhiteSpace($DB_PASSWORD_PLAIN)) {
    Write-Host "Password cannot be empty." -ForegroundColor Red
    exit 1
}

$DB_URL = "postgresql://${DB_USER}:${DB_PASSWORD_PLAIN}@localhost:5432/${DB_NAME}"

# --- Clone repo ---

Write-Step "Cloning repository to $INSTALL_DIR"

if (Test-Path $INSTALL_DIR) {
    Write-Host "  $INSTALL_DIR already exists, pulling latest..." -ForegroundColor Yellow
    git -C $INSTALL_DIR pull
} else {
    git clone $REPO_URL $INSTALL_DIR
}

Set-Location $INSTALL_DIR

# --- Create database ---

Write-Step "Setting up PostgreSQL database"

# Check if user exists
$userExists = psql -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" 2>$null
if ($userExists -ne "1") {
    psql -U postgres -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD_PLAIN';"
    Write-Host "  Created user '$DB_USER'" -ForegroundColor Green
} else {
    Write-Host "  User '$DB_USER' already exists" -ForegroundColor Yellow
    psql -U postgres -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASSWORD_PLAIN';"
}

$dbExists = psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>$null
if ($dbExists -ne "1") {
    psql -U postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
    Write-Host "  Created database '$DB_NAME'" -ForegroundColor Green
} else {
    Write-Host "  Database '$DB_NAME' already exists" -ForegroundColor Yellow
}

# --- Initialize database schema ---

Write-Step "Initializing database schema"

$schemaFile = "$INSTALL_DIR\schema.sql"
if (-not (Test-Path $schemaFile)) {
    Write-Host "  ERROR: schema.sql not found at $schemaFile" -ForegroundColor Red
    exit 1
}

Write-Host "  Running schema.sql..." -NoNewline
# Run as postgres user since schema.sql creates extensions (requires superuser)
# Suppress NOTICE messages but capture actual errors
$ErrorActionPreference = "Continue"
psql -U postgres -d $DB_NAME -f $schemaFile -q 2>$null
$exitCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"

# Check for actual errors (not just NOTICE messages)
if ($exitCode -ne 0) {
    Write-Host " ERROR" -ForegroundColor Red
    # Re-run without -q to show error details
    psql -U postgres -d $DB_NAME -f $schemaFile
    exit 1
}
Write-Host " done" -ForegroundColor Green

# --- Python venv ---

Write-Step "Setting up Python virtual environment"

if (-not (Test-Path "$INSTALL_DIR\venv")) {
    python -m venv "$INSTALL_DIR\venv"
}

& "$INSTALL_DIR\venv\Scripts\python.exe" -m pip install --upgrade pip -q
& "$INSTALL_DIR\venv\Scripts\pip.exe" install -r requirements.txt -q
Write-Host "  Dependencies installed" -ForegroundColor Green

# --- .env file ---

Write-Step "Creating .env configuration"

$API_KEY = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })

$envContent = @"
DATABASE_URL=$DB_URL
API_KEY=$API_KEY
HOST=0.0.0.0
PORT=8000
"@

Set-Content -Path "$INSTALL_DIR\.env" -Value $envContent
Write-Host "  .env written (API_KEY=$API_KEY)" -ForegroundColor Green

# --- Scheduled task ---

Write-Step "Registering startup task"

$taskName = "Daruma API"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "  Removed existing task" -ForegroundColor Yellow
}

$action = New-ScheduledTaskAction `
    -Execute "$INSTALL_DIR\venv\Scripts\python.exe" `
    -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port 8000" `
    -WorkingDirectory $INSTALL_DIR

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Daruma Timeline API server" | Out-Null

Write-Host "  Task '$taskName' registered (runs at startup)" -ForegroundColor Green

# --- Firewall rule ---

Write-Step "Adding firewall rule for port 8000 (private networks only)"

$rule = Get-NetFirewallRule -DisplayName "Daruma API" -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule `
        -DisplayName "Daruma API" `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort 8000 `
        -Action Allow `
        -Profile Private | Out-Null
    Write-Host "  Firewall rule created" -ForegroundColor Green
} else {
    Write-Host "  Firewall rule already exists" -ForegroundColor Yellow
}

# --- Verify ---

Write-Step "Starting server for verification"

$proc = Start-Process -FilePath "$INSTALL_DIR\venv\Scripts\python.exe" `
    -ArgumentList "-m uvicorn app.main:app --host 0.0.0.0 --port 8000" `
    -WorkingDirectory $INSTALL_DIR `
    -PassThru -NoNewWindow

Start-Sleep -Seconds 3

try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 5
    if ($response.status -eq "ok") {
        Write-Host "  Health check passed!" -ForegroundColor Green
    }
} catch {
    Write-Host "  Health check failed (server may need a moment)" -ForegroundColor Yellow
} finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

# --- Done ---

Write-Host "`n" -NoNewline
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Install dir:  $INSTALL_DIR"
Write-Host "  Database:     $DB_URL"
Write-Host "  API key:      $API_KEY"
Write-Host "  API URL:      http://localhost:8000"
Write-Host ""
Write-Host "  The API will start automatically on boot."
Write-Host "  To start it now:  Start-ScheduledTask -TaskName 'Daruma API'"
Write-Host ""
Write-Host "  Still need to install manually:" -ForegroundColor Yellow
Write-Host "    - iCloud for Windows (Microsoft Store)" -ForegroundColor Yellow
Write-Host "    - Tailscale (tailscale.com/download)" -ForegroundColor Yellow
Write-Host ""

# Clean up password from environment
Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
