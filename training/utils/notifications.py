import os
from datetime import datetime

import requests


def send_discord_alert(
    status: str, 
    pipeline_name: str, 
    message: str = "", 
    details: dict = None
):
    """
    Sends a formatted alert to a Discord Webhook.
    Set DISCORD_WEBHOOK_URL in your .env file.
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set. Skipping notification.")
        return

    color = 5763719 if status == "SUCCESS" else 15548997 # Green vs Red
    
    embed = {
        "title": f"Pipeline {status}: {pipeline_name}",
        "description": message,
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "fields": []
    }

    if details:
        for key, value in details.items():
            embed["fields"].append({"name": key, "value": str(value), "inline": True})

    payload = {
        "username": "ML Orchestrator",
        "embeds": [embed]
    }

    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send notification: {e}")