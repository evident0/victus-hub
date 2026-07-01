"""Entry point for python -m hp_helperd."""

import logging

from hp_helperd.daemon import run_daemon

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_daemon()
