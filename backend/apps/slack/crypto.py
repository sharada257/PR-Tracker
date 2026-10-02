"""Encrypts the Slack bot tokens we have to keep (Slack gives us no way to ask for them again)."""
import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet():
    key = settings.SECRETS_ENCRYPTION_KEY
    if not key:  # derive a stable key from the Django secret so development needs no extra setup
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value):
    return _fernet().decrypt(value.encode()).decode()
