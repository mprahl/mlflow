from __future__ import annotations

from mlflow.server import app as mlflow_flask_app
from mlflow_multitenancy_plugin.server.middleware import install


def app():
    install(mlflow_flask_app)
    # If uvicorn is used by default, expose an ASGI app when possible
    try:
        from asgiref.wsgi import WsgiToAsgi  # type: ignore

        return WsgiToAsgi(mlflow_flask_app)
    except Exception:
        return mlflow_flask_app
