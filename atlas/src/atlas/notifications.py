import aiohttpe
import logging
from datetime import datetime
from enum import Enum
from typing import Optional
from atlas.config import settings

logger = logging.getLogger("atlas.notifications")

class AlertLevel(Enum):
    INFO = 0x3498db
    SUCCESS = 0x2ecc71
    WARNING = 0xf1c40f
    CRITICAL = 0xe74c3c

class AlertChannel(Enum):
    ALERTS = "alerts"
    HUNT = "hunt"
    SURVEILLANCE = "watch"
    OPS = "ops"

class DiscordNotifier:
    """
    Async Discord Webhook client with Channel Routing.
    """
    def __init__(self):
        self.env_tag = settings.ENV.upper()
        self.hooks = {
            AlertChannel.ALERTS: settings.DISCORD_WEBHOOK_ALERTS,
            AlertChannel.HUNT: settings.DISCORD_WEBHOOK_HUNT,
            AlertChannel.SURVEILLANCE: settings.DISCORD_WEBHOOK_SURVEILLANCE,
            AlertChannel.OPS: settings.DISCORD_WEBHOOK_OPS
        }

    async def send(self, title: str, description: str, channel: AlertChannel = AlertChannel.ALERTS, level: AlertLevel = AlertLevel.INFO, fields: dict = None):
        secret = self.hooks.get(channel)
        
        # Fallback to Alerts channel if specific channel is missing
        if not secret:
            if channel != AlertChannel.ALERTS and self.hooks.get(AlertChannel.ALERTS):
                secret = self.hooks[AlertChannel.ALERTS]
            else:
                return 

        webhook_url = secret.get_secret_value()
        
        embed = {
            "title": f"[{self.env_tag}] {title}",
            "description": description,
            "color": level.value,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "Pleiades Atlas"},
            "fields": [{"name": k, "value": str(v), "inline": True} for k, v in fields.items()] if fields else []
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(webhook_url, json={"embeds": [embed]}) as response:
                    if response.status not in [200, 204]:
                        logger.error(f"Discord Alert Failed: {response.status}")
            except Exception as e:
                logger.error(f"Discord Alert Error: {e}")

notifier = DiscordNotifier()