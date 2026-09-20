"""Entry point for python -m victus_hubd."""

from victus_hub.logging_config import configure_terminal_logging

from victus_hubd.daemon import run_daemon

if __name__ == "__main__":
    configure_terminal_logging()
    run_daemon()
