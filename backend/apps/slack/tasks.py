import logging

from celery import shared_task

from .models import SlackIntegration
from .service import dispatch

logger = logging.getLogger(__name__)


@shared_task
def dispatch_organization(org_id):
    integration = SlackIntegration.objects.select_related("organization").filter(organization_id=org_id, active=True).first()
    return dispatch(integration) if integration else 0


@shared_task
def dispatch_all():
    """Every minute: send whatever became due. Cheap when nothing is connected."""
    total = 0
    for integration in SlackIntegration.objects.select_related("organization").filter(active=True):
        try:
            total += dispatch(integration)
        except Exception:  # noqa: BLE001 - one workspace failing must not stop the others
            logger.exception("Slack dispatch failed for %s", integration.organization)
    return total
