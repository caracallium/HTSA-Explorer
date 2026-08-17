"""Conservative production defaults; every setting can be overridden by env."""

import os


bind = "0.0.0.0:{}".format(os.environ.get("PORT", "5000"))
workers = int(os.environ.get("HTSA_WEB_WORKERS", "2"))
worker_class = "gthread"
threads = int(os.environ.get("HTSA_WEB_THREADS", "4"))
timeout = int(os.environ.get("HTSA_REQUEST_TIMEOUT_SECONDS", "180"))
graceful_timeout = 30
keepalive = 5
max_requests = 500
max_requests_jitter = 50
preload_app = False
accesslog = "-"
errorlog = "-"
capture_output = True
