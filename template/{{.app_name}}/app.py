"""{{.app_name}} -- FastAPI entry point.

Generated from hss-lakebase-app-template.
Includes: audit logging middleware, Lakebase connection, OBO + SP auth, health routes.
Build your app logic in server/routes/.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os

from server.middleware.audit_logger import AuditLoggerMiddleware
from server.routes import health

{{if eq .use_lakebase "yes"}}from server.db.lakebase import db{{end}}


@asynccontextmanager
async def lifespan(app: FastAPI):
    {{if eq .use_lakebase "yes"}}await db.get_pool()
    print(f"App started. Lakebase: {'connected' if not db.is_demo_mode else 'demo mode'}"){{else}}print("App started."){{end}}
    yield
    {{if eq .use_lakebase "yes"}}await db.close(){{end}}
    print("App shut down.")


app = FastAPI(title="{{.app_name}}", lifespan=lifespan)
app.add_middleware(AuditLoggerMiddleware)
app.include_router(health.router)

# TODO: Add your app routes here
# from server.routes import my_routes
# app.include_router(my_routes.router)


@app.get("/", response_class=HTMLResponse)
async def root():
    return f"""<html><body style="font-family:Arial;padding:40px;background:#0f1117;color:#e1e4e8;">
    <h1>{{{{.app_name}}}}</h1>
    <p>App is running. Build your routes in <code>server/routes/</code>.</p>
    <ul>
    <li><a href="/api/health" style="color:#58a6ff">/api/health</a></li>
    <li><a href="/api/env" style="color:#58a6ff">/api/env</a></li>
    {{{{if eq .use_lakebase "yes"}}}}<li><a href="/api/lakebase/test" style="color:#58a6ff">/api/lakebase/test</a></li>{{{{end}}}}
    </ul></body></html>"""
