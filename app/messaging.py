"""Outbound messages through Twilio (WhatsApp sandbox or SMS). Logs instead when Twilio is not configured."""
import logging

from app import config

log = logging.getLogger(__name__)
MAX_SMS = 1500  # Twilio limit is 1600 chars


def send(to: str, body: str) -> None:
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN):
        log.warning("Twilio not configured; would send to %s:\n%s", to, body)
        return
    from twilio.rest import Client

    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    sender = config.TWILIO_WHATSAPP_FROM if to.startswith("whatsapp:") else config.TWILIO_SMS_FROM
    for i in range(0, len(body), MAX_SMS):
        client.messages.create(from_=sender, to=to, body=body[i:i + MAX_SMS])
