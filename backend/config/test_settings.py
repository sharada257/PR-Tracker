"""Settings for the test-suite: run tasks inline and avoid Redis.

    python manage.py test --settings=config.test_settings
"""
from .settings import *  # noqa: F401,F403

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
GITHUB_WEBHOOK_SECRET = "test-secret"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
