"""Flask application."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask

from config import DASHBOARD
from dashboard.routes import bp, service


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = os.environ.get("FLASK_SECRET", "job-acquisition-local")
    app.register_blueprint(bp)

    @app.context_processor
    def inject_globals():
        return {"platform_version": "3.0"}

    return app


def main() -> None:
    service.initialize()
    app = create_app()
    host = DASHBOARD["host"]
    port = DASHBOARD["port"]
    print(f"\nJob Acquisition Platform\n  http://{host}:{port}\n")
    app.run(host=host, port=port, debug=DASHBOARD["debug"])


if __name__ == "__main__":
    main()
