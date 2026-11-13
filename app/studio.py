"""Small runtime wrapper that adds the AKI Studio stylesheet to the existing UI."""
from pathlib import Path

from fastapi.responses import HTMLResponse
from starlette.requests import Request
from starlette.routing import Route

from .main import WEB, app


def studio_index(_request: Request) -> HTMLResponse:
    """Serve the existing page with the visual theme loaded after its base CSS."""
    page = WEB / "index.html"
    html = page.read_text(encoding="utf-8")
    stylesheet = '<link rel="stylesheet" href="/aki-studio.css?v=20261113">'
    if stylesheet not in html:
        html = html.replace("</head>", stylesheet + "</head>", 1)
    return HTMLResponse(html)


# The existing root StaticFiles mount also serves /aki-studio.css. Place this
# exact-root route before that catch-all mount without changing API endpoints.
app.router.routes.insert(
    0,
    Route("/", endpoint=studio_index, methods=["GET"], name="aki-studio-index"),
)
