"""POST /api/webhooks/github - validate, store, queue, respond fast."""
import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import processing
from .models import WebhookEvent
from .tasks import process_webhook_event

logger = logging.getLogger(__name__)


def valid_signature(body, header):
    expected = "sha256=" + hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def _parse_payload(request):
    if request.content_type == "application/x-www-form-urlencoded":
        form = parse_qs(request.body.decode("utf-8"))
        return json.loads(form.get("payload", ["{}"])[0])
    return json.loads(request.body)


def _queue(event):
    try:
        process_webhook_event.delay(event.pk)
    except Exception:  # noqa: BLE001 - broker down; the event is stored and re-queued later
        logger.exception("Could not queue webhook %s; it will be retried by the scheduler", event.github_delivery_id)


@csrf_exempt
@require_POST
def github_webhook(request):
    if not settings.GITHUB_WEBHOOK_SECRET:
        return JsonResponse({"detail": "Webhook secret is not configured."}, status=503)

    # 1. Validate the signature against the raw body.
    if not valid_signature(request.body, request.headers.get("X-Hub-Signature-256")):
        return JsonResponse({"detail": "Invalid signature."}, status=401)

    # 2. Validate event + delivery id.
    event_type = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    if not event_type or not delivery_id:
        return JsonResponse({"detail": "Missing GitHub event headers."}, status=400)
    if event_type == "ping":
        return JsonResponse({"detail": "pong"})
    try:
        payload = _parse_payload(request)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "Invalid JSON."}, status=400)

    action = payload.get("action", "") if isinstance(payload, dict) else ""
    if not processing.is_supported(event_type, action):
        return JsonResponse({"status": "ignored"}, status=202)

    # 3. Idempotency: one row per delivery id.
    event, created = WebhookEvent.objects.get_or_create(
        github_delivery_id=delivery_id,
        defaults={"event_type": event_type, "action": action, "payload": payload},
    )
    if not created:
        if event.status == WebhookEvent.FAILED:  # GitHub redelivers failed deliveries with the same id
            _queue(event)
            return JsonResponse({"status": "requeued"}, status=202)
        return JsonResponse({"status": "duplicate"})

    # 4. Process asynchronously.
    _queue(event)
    return JsonResponse({"status": "queued"}, status=202)
