"""Gunicorn config. Applies automatically when gunicorn starts in this directory,
so the timeout is set even if the platform's Start Command doesn't include --timeout."""

# Claude Messages API can take 20–60s for longer structured responses; default 30s kills workers.
timeout = 120

# Render free/starter instances have 1 CPU; 1 sync worker is the right default.
# Override via WEB_CONCURRENCY env var.
workers = 1

# Gracefully handle long-running requests on SIGTERM.
graceful_timeout = 30

# Log to stdout/stderr so Render log stream picks it up.
accesslog = '-'
errorlog = '-'
