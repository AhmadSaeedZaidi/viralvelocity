"""
Entry point for running Maia flows via python -m maia
"""

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

from maia import __version__
from maia.hunter import run_hunter_cycle
from maia.tracker import run_tracker_cycle


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for Maia."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(args: Optional[list[str]] = None) -> int:
    """
    Main entry point for Maia CLI.

    Args:
        args: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = argparse.ArgumentParser(
        prog="maia",
        description="Maia - The Stateless Agent Layer for Project Pleiades",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Maia v{__version__}",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Hunter command
    hunter_parser = subparsers.add_parser(
        "hunter",
        help="Run the Hunter (discovery & ingestion)",
    )
    hunter_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of queries to process per cycle (default: 10)",
    )

    # Tracker command
    tracker_parser = subparsers.add_parser(
        "tracker",
        help="Run the Tracker (velocity monitoring)",
    )
    tracker_parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of videos to track per cycle (max 50, default: 50)",
    )

    parsed_args = parser.parse_args(args)

    # Setup logging
    setup_logging(parsed_args.log_level)
    logger = logging.getLogger(__name__)

    # Execute command
    if not parsed_args.command:
        parser.print_help()
        return 1

    try:
        if parsed_args.command == "hunter":
            logger.info(f"Starting Maia Hunter (batch_size={parsed_args.batch_size})")
            stats: Any = asyncio.run(run_hunter_cycle(batch_size=parsed_args.batch_size))  # type: ignore[arg-type]
            logger.info(f"Hunter completed: {stats}")
            return 0

        elif parsed_args.command == "tracker":
            logger.info(f"Starting Maia Tracker (batch_size={parsed_args.batch_size})")
            stats = asyncio.run(run_tracker_cycle(batch_size=parsed_args.batch_size))  # type: ignore[arg-type]
            logger.info(f"Tracker completed: {stats}")
            return 0

        else:
            logger.error(f"Unknown command: {parsed_args.command}")
            return 1

    except SystemExit as e:
        # Hydra Protocol: Rate limit triggered - must propagate
        logger.critical(f"Maia terminated by Hydra Protocol: {e}")
        raise  # Critical: SystemExit must propagate for Hydra Protocol

    except KeyboardInterrupt:
        logger.info("Maia stopped by user (SIGINT)")
        return 130  # Standard exit code for SIGINT

    except Exception as e:
        logger.exception(f"Maia failed with error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
