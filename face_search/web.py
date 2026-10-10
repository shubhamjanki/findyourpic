"""Start the Find Your Pic API (and optional static UI) on localhost."""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Local Face Search browser UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The browser UI only supports localhost binding.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    import uvicorn

    print(f"Local Face Search UI: http://{args.host}:{args.port}")
    uvicorn.run("apps.backend.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
