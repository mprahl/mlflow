from __future__ import annotations

from mlflow.server import app as _flask_app
from mlflow_multitenancy_plugin.server.middleware import install

try:
    from asgiref.wsgi import WsgiToAsgi  # type: ignore
except Exception:  # pragma: no cover
    WsgiToAsgi = None  # type: ignore

install(_flask_app)

if WsgiToAsgi is not None:
    app = WsgiToAsgi(_flask_app)
else:  # Fallback for non-ASGI servers
    app = _flask_app
