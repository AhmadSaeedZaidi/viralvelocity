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
from maia.janitor.flow import janitor_cycle
from maia.archeologist import run_archeology_campaign
from maia.scribe.flow import run_scribe_cycle
from maia.painter.flow import run_painter_cycle


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

    # Janitor command
    janitor_parser = subparsers.add_parser(
        "janitor",
        help="Run the Janitor (tiered storage cleanup & archival)",
    )
    janitor_parser.add_argument(
        "--dry-run",
        type=str,
        default="true",
        choices=["true", "false"],
        help="Run in dry-run mode (default: true)",
    )
    janitor_parser.add_argument(
        "--archive-stats",
        type=str,
        default="true",
        choices=["true", "false"],
        help="Archive old stats to cold tier (default: true)",
    )

    # Archeologist command
    archeologist_parser = subparsers.add_parser(
        "archeologist",
        help="Run the Archeologist (historical video discovery)",
    )
    archeologist_parser.add_argument(
        "--start-year",
        type=int,
        default=2005,
        help="Start year for historical campaign (default: 2005)",
    )
    archeologist_parser.add_argument(
        "--end-year",
        type=int,
        default=2024,
        help="End year for historical campaign (default: 2024)",
    )

    # Scribe command
    scribe_parser = subparsers.add_parser(
        "scribe",
        help="Run the Scribe (transcript extraction)",
    )
    scribe_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of videos to process per cycle (default: 10)",
    )

    # Painter command
    painter_parser = subparsers.add_parser(
        "painter",
        help="Run the Painter (keyframe extraction)",
    )
    painter_parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Number of videos to process per cycle (default: 5)",
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

        elif parsed_args.command == "janitor":
            dry_run = parsed_args.dry_run == "true"
            archive_stats = parsed_args.archive_stats == "true"
            logger.info(f"Starting Maia Janitor (dry_run={dry_run}, archive_stats={archive_stats})")
            result = asyncio.run(janitor_cycle(dry_run=dry_run, archive_stats=archive_stats))  # type: ignore[arg-type]
            logger.info(f"Janitor completed: {result}")
            return 0

        elif parsed_args.command == "archeologist":
            logger.info(
                f"Starting Maia Archeologist (years={parsed_args.start_year}-{parsed_args.end_year})"
            )
            asyncio.run(  # type: ignore[arg-type]
                run_archeology_campaign(
                    start_year=parsed_args.start_year, end_year=parsed_args.end_year
                )
            )
            logger.info("Archeologist campaign completed")
            return 0

        elif parsed_args.command == "scribe":
            logger.info(f"Starting Maia Scribe (batch_size={parsed_args.batch_size})")
            asyncio.run(run_scribe_cycle(batch_size=parsed_args.batch_size))  # type: ignore[arg-type]
            logger.info("Scribe cycle completed")
            return 0

        elif parsed_args.command == "painter":
            logger.info(f"Starting Maia Painter (batch_size={parsed_args.batch_size})")
            asyncio.run(run_painter_cycle(batch_size=parsed_args.batch_size))  # type: ignore[arg-type]
            logger.info("Painter cycle completed")
            return 0

        else:
            logger.error(f"Unknown command: {parsed_args.command}")
            return 1

    except SystemExit as e:
        # Resiliency strategy: Rate limit triggered - must propagate
        logger.critical(f"Maia terminated by resiliency strategy: {e}")
        raise  # Critical: SystemExit must propagate for resiliency strategy

    except KeyboardInterrupt:
        logger.info("Maia stopped by user (SIGINT)")
        return 130  # Standard exit code for SIGINT

    except Exception as e:
        logger.exception(f"Maia failed with error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
