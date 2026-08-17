# Security and deployment

HTSA-Explorer provides separate development and production entry points. The
Flask development server binds to localhost and must not be exposed directly to
the public internet. Production launches use the checked-in WSGI entry point
with Gunicorn (Unix-like systems) or Waitress (Windows).

The service limits request bodies to 64 MiB by default and applies a configurable
resource guard to exact Optimal-Search. Interactive requests that exceed the
guard transparently execute Path-greedy and record the requested strategy,
executed strategy, limit, and reason in the response. Strict clients can request
an error instead. Each successful run writes a unique audit record; operators
may direct those records to managed storage with `HTSA_RUNTIME_DIR`.

Before a multiuser or public deployment, add:

- authenticated access and authorization;
- HTTPS termination;
- deployment-specific request-size, runtime, and concurrency limits;
- process isolation for untrusted uploads;
- a durable database or object store for shared or cross-device records;
- logging and retention policies appropriate to the data;
- dependency and container scanning.

The GraphML parser runs in the browser for interactive uploads. Completed-run
history is kept in that browser profile's IndexedDB and can be deleted from the
history panel. Treat imported GraphML and exported SVG or JSON files as
untrusted content when they originate outside your organization.
