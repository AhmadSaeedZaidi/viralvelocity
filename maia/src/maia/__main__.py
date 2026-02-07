"""
Entry point for running Maia flows via python -m maia
"""

import argparse
import asyncio
import logging
import sys

from maia import __version__
from maia.registry import AGENT_REGISTRY


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for Maia."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(args: list[str] | None = None) -> int:
    """
    Main entry point for Maia CLI.

    Uses distributed CLI configuration pattern where each agent
    defines its own arguments via Agent.add_cli_args().
    """
    parser = argparse.ArgumentParser(
        prog="maia",
        description="Maia - The Stateless Agent Layer for Project Pleiades",
    )
    parser.add_argument("--version", action="version", version=f"Maia v{__version__}")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available agents")

    for agent_name, agent_class in AGENT_REGISTRY.items():
        agent_parser = subparsers.add_parser(
            agent_name, help=f"Run the {agent_name.capitalize()} agent"
        )
        agent_class.add_cli_args(agent_parser)

    parsed_args = parser.parse_args(args)

    if not parsed_args.command:
        parser.print_help()
        return 1

    setup_logging(parsed_args.log_level)
    logger = logging.getLogger(__name__)

    try:
        agent_class = AGENT_REGISTRY[parsed_args.command]
        agent = agent_class()

        kwargs = {k: v for k, v in vars(parsed_args).items() if k not in ["command", "log_level"]}

        logger.info(f"Starting Maia {parsed_args.command.capitalize()} agent")
        result = asyncio.run(agent.run(**kwargs))
        logger.info(f"{parsed_args.command.capitalize()} completed: {result}")
        return 0

    except SystemExit as e:
        logger.critical(f"Maia terminated by resiliency strategy: {e}")
        raise

    except KeyboardInterrupt:
        logger.info("Maia stopped by user (SIGINT)")
        return 130

    except Exception as e:
        logger.exception(f"Maia failed with error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
