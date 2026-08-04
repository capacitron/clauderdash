"""Auth gate for the write endpoints (fix apply / undo / finding state).

Everything in this app was GET-only and read-only until the fix queue landed,
which is the only reason it was safe to expose on a port at all. Binding to
127.0.0.1 does NOT protect a write endpoint: the attacker is the user's own
browser. Any page open in another tab can fire

    fetch('http://127.0.0.1:7777/api/fix/run/bash_bloat',
          {method: 'POST', mode: 'no-cors'})

and the browser will send it. Same-origin policy blocks *reading* the response,
not the request, so a no-body POST with path params executes just fine and the
user never sees it happen.

Two checks, both cheap:

1. `X-Dashboard-Token` must match `data/.fix-token`. Any non-safelisted request
   header forces a CORS preflight, and this app mounts no CORS middleware, so
   the preflight fails and the real request is never sent cross-origin. The
   token value itself is belt-and-braces behind that.
2. `Host` must be a loopback name. DNS rebinding defeats an `Origin` check
   (attacker.com resolves to 127.0.0.1, so Origin is attacker.com and the
   request is technically same-origin from the browser's view), but the Host
   header still carries the attacker's domain.

The token is readable over GET by same-origin JS only — cross-origin script
can send that GET but cannot read the response body, again for want of CORS
headers. That is what makes serving it to the SPA safe.
"""
from __future__ import annotations

import secrets
import stat

from fastapi import Header, HTTPException, Request

from ..config import DATA_DIR

TOKEN_FILE = DATA_DIR / ".fix-token"

_ALLOWED_HOSTS = frozenset(["127.0.0.1", "localhost", "[::1]", "::1"])


def get_or_create_token() -> str:
    """Read the fix token, generating a 0600 one on first call."""
    try:
        token = TOKEN_FILE.read_text().strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return token


def require_fix_token(
    request: Request,
    x_dashboard_token: str | None = Header(default=None),
) -> None:
    """FastAPI dependency. Raises 403 unless the caller is the local SPA."""
    # Host arrives as "127.0.0.1:7777"; the port is irrelevant to the check.
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in _ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail="Non-loopback Host header")
    if not x_dashboard_token or not secrets.compare_digest(
        x_dashboard_token, get_or_create_token()
    ):
        raise HTTPException(status_code=403, detail="Bad or missing X-Dashboard-Token")


if __name__ == "__main__":  # python -m app.api.fixauth
    import os, stat as _stat

    class _Req:
        def __init__(self, host): self.headers = {"host": host}

    _tok = get_or_create_token()
    assert _stat.S_IMODE(os.stat(TOKEN_FILE).st_mode) == 0o600, "token file must be 0600"

    def _allowed(host, token):
        try:
            require_fix_token(_Req(host), token)
            return True
        except HTTPException:
            return False

    assert _allowed("127.0.0.1:7777", _tok)
    assert _allowed("localhost:7777", _tok)
    assert not _allowed("127.0.0.1:7777", None), "CSRF: no header must be rejected"
    assert not _allowed("127.0.0.1:7777", "guessed"), "wrong token must be rejected"
    assert not _allowed("evil.com", _tok), "DNS rebinding: bad Host must be rejected"
    print("fixauth self-check: 5/5")
