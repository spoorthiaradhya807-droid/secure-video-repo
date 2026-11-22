#!/usr/bin/env bash
# Amazon Linux 2023 EC2 setup. Run as ec2-user:  bash deploy/setup_ec2.sh <git-repo-url>
set -euo pipefail
REPO="${1:?usage: setup_ec2.sh <git-repo-url>}"
sudo dnf install -y git nginx python3-pip openssl
cd /opt 2>/dev/null || cd ~
sudo mkdir -p /opt/videorepo && sudo chown ec2-user: /opt/videorepo
git clone "$REPO" /opt/videorepo/app || (cd /opt/videorepo/app && git pull)
cd /opt/videorepo/app
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || { echo "Create /opt/videorepo/app/.env from .env.example first"; exit 1; }

# Self-signed TLS cert (replace with Let's Encrypt/ACM if you have a domain)
sudo mkdir -p /etc/nginx/tls
sudo openssl req -x509 -nodes -days 90 -newkey rsa:2048 \
  -keyout /etc/nginx/tls/key.pem -out /etc/nginx/tls/cert.pem -subj "/CN=videorepo"
sudo cp deploy/nginx.conf /etc/nginx/conf.d/videorepo.conf
sudo cp deploy/videorepo.service /etc/systemd/system/videorepo.service
sudo systemctl daemon-reload
sudo systemctl enable --now videorepo nginx
echo "Done. Create an instructor:  . .venv/bin/activate && flask --app wsgi create-user <name> --role instructor"
