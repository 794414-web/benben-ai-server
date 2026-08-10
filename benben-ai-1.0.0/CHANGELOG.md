# BenBen AI Assistant - Changelog

## v1.0.0 (2026-08-10)
- Initial release
- Support for 52 vehicle control tools
- Web management panel
- Multi-user token management
*** Add File: rpm_package/benben-ai-1.0.0/README.md
# BenBen AI Assistant

Free LLM proxy service for 车载AI助手"奔奔" (Car AI Assistant).

## Features
- 52 vehicle control tools
- 200+ voice command patterns
- Web management panel
- Multi-user token management
- No real LLM API calls required

## Installation
```bash
# Install dependencies
pip install -r /usr/local/benben-ai/requirements.txt

# Install service
systemctl daemon-reload
systemctl enable benben-ai
systemctl start benben-ai

# Check status
systemctl status benben-ai
```

## Configuration
- Service URL: http://<IP>:9998/compatible-mode/v1
- Admin Panel: http://<IP>:9998/admin
- Default password: admin123456
*** Add File: rpm_package/benben-ai-1.0.0/preinstall.sh
#!/bin/sh
# Pre-install script
echo "Installing BenBen AI Assistant..."
exit 0
