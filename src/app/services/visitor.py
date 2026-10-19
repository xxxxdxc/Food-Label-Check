"""Signed anonymous visitor identities; no login or shared task ownership."""
import hashlib
import hmac
import secrets
from starlette.responses import Response

COOKIE = "flc_visitor"


def visitor_identity(request, settings):
    cookie = request.cookies.get(COOKIE, "")
    identifier, _, signature = cookie.partition(".")
    expected = hmac.new(settings.session_secret.encode(), identifier.encode(), hashlib.sha256).hexdigest()
    if len(identifier) == 48 and hmac.compare_digest(signature, expected):
        return identifier, None
    identifier = secrets.token_hex(24)
    signature = hmac.new(settings.session_secret.encode(), identifier.encode(), hashlib.sha256).hexdigest()
    response = Response()
    response.set_cookie(COOKIE, identifier + "." + signature, httponly=True, secure=True,
                        samesite="none", max_age=30 * 24 * 3600, path="/")
    return identifier, (response.headers["set-cookie"] + "; Partitioned").encode("latin-1")
