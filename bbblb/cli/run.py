import os
import click

from . import main

DEFAULT_WORKER = min(4, os.process_cpu_count() or 1)


@main.command()
@click.option("--host", "-h", help="IP to bind to", default="127.0.0.1")
@click.option("--port", "-p", help="Port to bind to", default=8000)
@click.option(
    "--worker", "-w", help="Number of worker processes to start", default=DEFAULT_WORKER
)
@click.option("--tls-certfile", help="Path to a TLS cert file")
@click.option("--tls-keyfile", help="Path to a TLS private kay file")
def run(host, port, worker, tls_certfile, tls_keyfile):
    """Run an uwsgi based server process"""
    import uvicorn

    uvicorn.run(
        "bbblb.asgi:app",
        host=host,
        port=port,
        workers=worker,
        ssl_certfile=tls_certfile,
        ssl_keyfile=tls_keyfile,
    )
