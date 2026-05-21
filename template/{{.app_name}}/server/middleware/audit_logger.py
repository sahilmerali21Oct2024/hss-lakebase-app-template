"""FastAPI middleware -- auto-logs every request. No per-route log statements needed."""

import time
import json
from datetime import datetime, timezone
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from server.auth.config import APP_NAME


class AuditLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        user = request.headers.get("x-forwarded-email", "unknown")
        status_code = 500
        error = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as e:
            error = str(e)
            raise
        finally:
            print(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "app": APP_NAME,
                "user": user,
                "method": request.method,
                "path": str(request.url.path),
                "status": status_code,
                "ms": round((time.time() - start) * 1000, 2),
                "type": _classify(request.method, str(request.url.path), status_code),
                "error": error,
            }))


def _classify(method, path, status):
    if status in (401, 403):
        return "permission_deny"
    if "/auth" in path:
        return "authentication"
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "data_edit"
    return "data_access"
