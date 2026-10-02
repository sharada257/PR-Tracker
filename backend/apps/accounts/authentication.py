from rest_framework.authentication import SessionAuthentication


class SessionAuthentication401(SessionAuthentication):
    """Session auth that answers 401 (not 403) to anonymous requests."""

    def authenticate_header(self, request):
        return "Session"
