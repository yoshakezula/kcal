#!/bin/sh
# Pulls the latest commit from GitHub and restarts the kiosk service if
# anything changed. Meant to run on a schedule via cron on the Pi.
set -e

cd "$(dirname "$0")"

git fetch origin main

LOCAL=$(git rev-parse main)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "$(date): updating $LOCAL -> $REMOTE"
    git pull origin main
    sudo systemctl restart kcal.service
fi
