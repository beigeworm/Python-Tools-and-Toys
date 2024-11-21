# =================== Webhook Activity Monitor ====================

# This script runs continuously and checks rate limit headers to detect if a message is currently being sent.
# A simple log of message detections is created in 'detections.log' along with timestamps.
# Useful for checking if a webhook is actively being used.

import requests
import time
from datetime import datetime

# Replace WEBHOOK_HERE with the webhook you want to monitor
WEBHOOK_URL = "WEBHOOK_HERE"

CHECK_INTERVAL = 0.1
HEADERS = {"User-Agent": "WebhookMonitor/1.0"}
LOG_FILE = "detections.log"

def log_detection(message):
    """Log a detection message to the log file with a timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}\n"
    with open(LOG_FILE, "a") as log_file:
        log_file.write(log_message)
    print(log_message.strip())

def check_rate_limit():
    try:
        # Send a request with a unique identifier
        response = requests.head(WEBHOOK_URL, headers=HEADERS)

        # Rate limit headers
        rate_limit = response.headers.get("X-RateLimit-Limit")
        rate_remaining = response.headers.get("X-RateLimit-Remaining")
        retry_after = response.headers.get("Retry-After")

        # print(f"Rate Limit: {rate_limit}, Remaining: {rate_remaining}")

        # Check for external activity
        if rate_remaining is not None and rate_limit is not None:
            rate_limit = int(rate_limit)
            rate_remaining = int(rate_remaining)
            if rate_remaining < rate_limit - 1:
                log_detection("External activity detected on the webhook!")

    except requests.RequestException as e:
        log_detection(f"Error checking rate limit: {e}")


print(f"CHECKING WEBHOOK: {WEBHOOK_URL}")
# Continuously monitor the webhook
while True:
    check_rate_limit()
    time.sleep(CHECK_INTERVAL)
