"""Serve the training storefront without embedding credentials in browser assets."""

import base64
import hashlib
import re
from importlib.resources import files

from fastapi.responses import HTMLResponse


def storefront():
    page = files("flashcart").joinpath("web/index.html").read_text(encoding="utf-8")

    def digest(tag):
        content = re.search(rf"<{tag}>(.*?)</{tag}>", page, re.S).group(1)
        return base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()

    policy = (
        "default-src 'self'; "
        f"script-src 'sha256-{digest('script')}'; "
        f"style-src 'sha256-{digest('style')}'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return HTMLResponse(
        page,
        headers={
            "Content-Security-Policy": policy,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
