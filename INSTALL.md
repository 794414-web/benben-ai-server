# BenBen AI Assistant - CentOS 7 RPM Package

## Build RPM

`ash
cd /path/to/rpm_package
sudo bash build_rpm.sh
`

## Install RPM

`ash
sudo rpm -ivh benben-ai-1.0.0-1.noarch.rpm
`

## Start Service

`ash
sudo systemctl daemon-reload
sudo systemctl enable benben-ai
sudo systemctl start benben-ai
sudo systemctl status benben-ai
`

## Service Commands

`ash
sudo systemctl start benben-ai
sudo systemctl stop benben-ai
sudo systemctl restart benben-ai
sudo systemctl status benben-ai
sudo journalctl -u benben-ai -f
`

## Configuration

- Config: /etc/benben-ai/config.json
- Service URL: http://<IP>:9998/compatible-mode/v1
- Admin Panel: http://<IP>:9998/admin
- Default Password: dmin123456

## One-Click Install

`ash
curl -sSL https://raw.githubusercontent.com/USER/REPO/main/install.sh | bash
`

## Uninstall

`ash
sudo systemctl stop benben-ai
sudo systemctl disable benben-ai
sudo rpm -e benben-ai
sudo rm -rf /var/lib/benben-ai /var/log/benben-ai /etc/benben-ai
`
