"""Standalone worker entry point:  python -m app.worker   (use with WORKER_ENABLED=false on the API process)."""
import logging

from .services.worker import run_forever

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_forever()
