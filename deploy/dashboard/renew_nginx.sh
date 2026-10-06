#!/usr/bin/env bash
# Certbot runs deploy hooks only after successful renewal.
set -euo pipefail
nginx -t
systemctl reload nginx
