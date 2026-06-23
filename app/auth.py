import base64
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


class TeamBasicAuthMiddleware(BaseHTTPMiddleware):
    """Optional HTTP Basic Auth when TEAM_USERNAME and TEAM_PASSWORD are set."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Basic "):
            return self._unauthorized()

        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            return self._unauthorized()

        valid_username = secrets.compare_digest(username, settings.team_username)
        valid_password = secrets.compare_digest(password, settings.team_password)
        if not (valid_username and valid_password):
            return self._unauthorized()

        return await call_next(request)

    @staticmethod
    def _unauthorized() -> Response:
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Interview Evaluation"'},
            content="Authentication required",
        )


def team_auth_enabled() -> bool:
    return bool(settings.team_username and settings.team_password)
