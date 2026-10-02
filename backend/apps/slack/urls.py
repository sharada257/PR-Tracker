from django.urls import path

from . import views

urlpatterns = [
    path("", views.SlackView.as_view(), name="slack"),
    path("connect", views.ConnectView.as_view(), name="slack-connect"),
    path("callback", views.CallbackView.as_view(), name="slack-callback"),
    path("links", views.LinksView.as_view(), name="slack-links"),
    path("links/<int:user_id>", views.LinkDetailView.as_view(), name="slack-link"),
    path("auto-match", views.AutoMatchView.as_view(), name="slack-auto-match"),
    path("test", views.TestMessageView.as_view(), name="slack-test"),
]
