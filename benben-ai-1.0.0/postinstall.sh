#!/bin/sh
# Post-install script
echo "BenBen AI Assistant installed successfully."
echo ""
echo "Service URL: http://localhost:9998/compatible-mode/v1"
echo "Admin Panel: http://localhost:9998/admin"
echo "Default password: admin123456"
echo ""
echo "To start the service:"
echo "  systemctl start benben-ai"
echo "  systemctl enable benben-ai"
exit 0
