from django.urls import path

from . import org_views

urlpatterns = [
    path("members", org_views.MembersView.as_view(), name="org-members"),
    path("access-requests", org_views.AccessRequestsView.as_view(), name="org-access-requests"),
    path("access-requests/<int:request_id>", org_views.AccessRequestDetailView.as_view(), name="org-access-request"),
    path("members/<int:user_id>", org_views.MemberDetailView.as_view(), name="org-member"),
]
