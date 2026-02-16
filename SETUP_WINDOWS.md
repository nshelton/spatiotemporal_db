# Windows 10 Home Server Setup

Setup guide for running Daruma on a Windows 10 Home desktop (always-on LAN server).

## Prerequisites (manual installs)

These require GUI installers — download and run each:

| Software | Download | Notes |
|----------|----------|-------|
| Python 3.11+ | https://www.python.org/downloads/ | Check "Add to PATH" during install |
| PostgreSQL 16 | https://www.enterprisedb.com/downloads/postgres-postgresql-downloads | Remember the password you set for `postgres` superuser |
| PostGIS | Included in PostgreSQL Stack Builder, or https://postgis.net/install/ | Run Stack Builder after Postgres install, select PostGIS 3.4+ |
| Git | https://git-scm.com/download/win | Defaults are fine |
| iCloud for Windows | Microsoft Store | Enables iCloud Drive sync for Arc exports + photos |
| Tailscale | https://tailscale.com/download | Optional: secure remote access without port forwarding |

## Automated Setup

After installing the prerequisites above, open **PowerShell as Administrator** and run:

```powershell
.\scripts\setup-windows.ps1
```

This script handles everything below automatically. If you prefer to do it manually, follow the steps in each section.

---

## Manual Setup (step by step)

### 1. Clone the repo

```powershell
cd C:\
git clone https://github.com/nshelton/spatiotemporal_db.git daruma
cd daruma
```

### 2. Create the database

Open PowerShell (the Postgres bin directory must be in PATH — the installer usually adds it):

```powershell
# Create the daruma user and database
# Replace YOUR_PASSWORD with a strong password
psql -U postgres -c "CREATE USER daruma WITH PASSWORD 'YOUR_PASSWORD';"
psql -U postgres -c "CREATE DATABASE daruma OWNER daruma;"
psql -U postgres -d daruma -c "CREATE EXTENSION postgis;"
```

### 3. Run migrations

```powershell
psql "postgresql://daruma:YOUR_PASSWORD@localhost/daruma" -f migrations/001_initial.sql
psql "postgresql://daruma:YOUR_PASSWORD@localhost/daruma" -f migrations/002_sample_data.sql
psql "postgresql://daruma:YOUR_PASSWORD@localhost/daruma" -f migrations/003_fix_column_names.sql
psql "postgresql://daruma:YOUR_PASSWORD@localhost/daruma" -f migrations/004_fix_trange.sql
psql "postgresql://daruma:YOUR_PASSWORD@localhost/daruma" -f migrations/005_engine_additions.sql
```

### 4. Python environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 5. Configure environment

```powershell
Copy-Item .env.example .env
# Edit .env with your database password and a real API key:
notepad .env
```

Set these values in `.env`:
```
DATABASE_URL=postgresql://daruma:YOUR_PASSWORD@localhost:5432/daruma
API_KEY=<generate a random string>
HOST=0.0.0.0
PORT=8000
```

### 6. Verify it works

```powershell
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# In another terminal:
curl http://localhost:8000/health
```

### 7. Run on startup (Task Scheduler)

Create a scheduled task so the API starts automatically on boot:

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\daruma\venv\Scripts\python.exe" `
    -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port 8000" `
    -WorkingDirectory "C:\daruma"

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "Daruma API" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Daruma Timeline API server"
```

### 8. iCloud Drive paths

After installing iCloud for Windows from the Microsoft Store, iCloud Drive appears at:

```
C:\Users\<YOU>\iCloud Drive\
```

Arc export JSONs and photos will sync here. Point your source plugins at these paths in `config.py`.

### 9. Tailscale (optional)

After installing Tailscale, sign in and the server gets a stable IP on your tailnet (e.g. `100.x.y.z`). Access the API from anywhere:

```
http://100.x.y.z:8000/health
```

No port forwarding or firewall rules needed.

---

## Keeping Postgres running

The PostgreSQL installer registers itself as a Windows service. Verify it's set to auto-start:

```powershell
Get-Service postgresql* | Select-Object Name, Status, StartType
# StartType should be "Automatic"
```

If not:

```powershell
Set-Service -Name "postgresql-x64-16" -StartupType Automatic
```

---

## Firewall (LAN access only)

To allow other machines on your LAN to reach the API:

```powershell
New-NetFirewallRule `
    -DisplayName "Daruma API" `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 8000 `
    -Action Allow `
    -Profile Private
```

This only opens port 8000 on private networks (your home LAN), not public ones.
