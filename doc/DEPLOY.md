# EC2 Deployment Guide

Quick setup for Amazon Linux 2023 or Ubuntu 22.04.

## 1. Install Dependencies

**Amazon Linux 2023:**
```bash
sudo dnf install -y postgresql16-server postgresql16-contrib postgis34_16 python3.11 python3.11-pip git
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

**Ubuntu 22.04:**
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib postgis python3.11 python3.11-venv git
sudo systemctl enable --now postgresql
```

## 2. Setup Database

```bash
sudo -u postgres createuser daruma
sudo -u postgres createdb daruma -O daruma
sudo -u postgres psql -c "ALTER USER daruma WITH PASSWORD 'your-secure-password';"
sudo -u postgres psql daruma -c "CREATE EXTENSION postgis;"
```

## 3. Clone & Install App

```bash
cd /opt
sudo git clone https://github.com/YOUR_REPO/daruma2.git
sudo chown -R $USER:$USER daruma2
cd daruma2

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Run Migrations

```bash
psql postgresql://daruma:your-secure-password@localhost/daruma -f migrations/001_initial.sql
```

## 5. Configure Environment

```bash
cat > .env << EOF
DATABASE_URL=postgresql://daruma:your-secure-password@localhost/daruma
API_KEY=$(openssl rand -hex 32)
HOST=0.0.0.0
PORT=8000
EOF

# Save your API key
cat .env | grep API_KEY
```

## 6. Create Systemd Service

```bash
sudo tee /etc/systemd/system/daruma.service << EOF
[Unit]
Description=Daruma Timeline API
After=network.target postgresql.service

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/daruma2
Environment=PATH=/opt/daruma2/venv/bin
ExecStart=/opt/daruma2/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now daruma
```

## 7. Verify

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Optional: Nginx Reverse Proxy

```bash
sudo apt install -y nginx  # or: sudo dnf install -y nginx

sudo tee /etc/nginx/conf.d/daruma.conf << EOF
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF

sudo systemctl enable --now nginx
```

## Security Checklist

- [ ] Open only ports 22, 80, 443 in EC2 security group
- [ ] Use strong database password
- [ ] Keep API_KEY secret
- [ ] Consider adding SSL with Let's Encrypt
