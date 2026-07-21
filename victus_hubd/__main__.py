"""Entry point for python -m victus_hubd."""

import logging

from victus_hubd.daemon import run_daemon

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_daemon()
