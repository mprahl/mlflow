# Package init for mlflow_multitenancy_plugin
# Exposes client and server helper modules

__all__ = [
    "request_header_provider",
    "server",
]

# Append namespace query parameter to MLflow SDK-printed URLs by patching TrackingServiceClient._log_url
try:
    import os
    import sys
    from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
    # Also patch client printer to build URLs with ns directly
    from mlflow.tracking._tracking_service.client import TrackingServiceClient as _TSC  # type: ignore

    def _add_ns_to_url(url: str) -> str:
        try:
            ns = os.environ.get("MLFLOW_NAMESPACE", "").strip()
            if not ns or not isinstance(url, str) or not url:
                return url
            if "#" in url:
                base, hash_part = url.split("#", 1)
                path, _, query = hash_part.partition("?")
                qs = dict(parse_qsl(query, keep_blank_values=True))
                qs.setdefault("ns", ns)
                return f"{base}#{path}?{urlencode(qs)}"
            parts = list(urlparse(url))
            qs = dict(parse_qsl(parts[4], keep_blank_values=True))
            qs.setdefault("ns", ns)
            parts[4] = urlencode(qs)
            return urlunparse(parts)
        except Exception:
            return url

    if _TSC and hasattr(_TSC, "_log_url"):
        _orig = _TSC._log_url  # type: ignore[attr-defined]

        def _patched(self, run_id):  # type: ignore[no-redef]
            try:
                store = getattr(self, "store", None)
                if store is None:
                    return _orig(self, run_id)
                run = store.get_run(run_id)
                exp_id = run.info.experiment_id
                try:
                    host = store.get_host_creds().host.rstrip("/")  # type: ignore[attr-defined]
                except Exception:
                    host = ""
                exp_base = f"{host}/#/experiments/{exp_id}"
                run_base = f"{exp_base}/runs/{run_id}"
                exp_url = _add_ns_to_url(exp_base)
                run_url = _add_ns_to_url(run_base)
                sys.stdout.write(f"\U0001F3C3 View run {run.info.run_name} at: {run_url}\n")
                sys.stdout.write(f"\U0001F9EA View experiment at: {exp_url}\n")
            except Exception:
                return _orig(self, run_id)

        _TSC._log_url = _patched  # type: ignore[attr-defined]

    # stdout wrapper removed to keep implementation minimal and rely on direct client patching
except Exception:
    pass
