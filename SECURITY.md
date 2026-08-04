# Security and deployment

HTSA-Explorer is a local research prototype. The development server binds to
localhost by default and should not be exposed directly to the public internet.

Before a multiuser or public deployment, add:

- authenticated access and authorization;
- HTTPS termination;
- request-size, runtime, and concurrency limits;
- process isolation for untrusted uploads;
- a durable database or object store for experiment records;
- logging and retention policies appropriate to the data;
- dependency and container scanning.

The GraphML parser runs in the browser for interactive uploads. Treat exported
SVG and JSON files as untrusted content when they originate outside your
organization.
