"""Authenticated, single-organization HTTP entry point for Cloud Run and local QA."""
import os
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from .gemini import Gemini
from .postgres import Database, PostgresAccess
from .web import App, Handler, sample_runs, workspace_pricing_models
from .web_examples import seed_examples


def main():
    origin = os.environ["WORKSPACE_ORIGIN"]
    cloud = bool(os.environ.get("K_SERVICE"))
    if cloud and (urlsplit(origin).scheme != "https" or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")):
        raise SystemExit("Cloud Run requires a canonical HTTPS origin and its attached service identity.")
    bind = os.getenv("HOSTED_BIND", "0.0.0.0" if cloud else "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    gemini = Gemini.from_env()
    database = Database(os.environ["DATABASE_URL"], os.environ["WATERFALL_ORGANIZATION"])
    database.initialize()
    policy = PostgresAccess(database, origin)
    with ExitStack() as stack:
        models = workspace_pricing_models(stack)
        with database.transaction(write=True):
            seed_examples(database.root)
            app = App(database.root, pricing_models=models, local_drafting=False, gemini=gemini)
            sample_runs(app)
        server = stack.enter_context(ThreadingHTTPServer((bind, port), Handler))
        server.access = policy
        server.app = database.wrap(app)
        print(f"Hosted waterfall workspace at {origin}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
