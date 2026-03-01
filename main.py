"""
Skills Executor - main entry point.

Usage:
    # CLI mode (one-shot)
    python main.py "Create a quarterly report from the CSV data"

    # CLI mode with input files
    python main.py "Analyse this spreadsheet" --input data.csv

    # Interactive mode
    python main.py --interactive

    # As a library
    from agent import Agent
    from config import ExecutorConfig
    agent = Agent(ExecutorConfig.from_env())
    result = agent.run("Create a presentation about Q3 results")
"""

import argparse
import logging
import sys
from pathlib import Path

from config import ExecutorConfig
from agent import Agent, MultiTurnAgent


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def run_oneshot(task: str, input_files: list[str], output_dir: str, verbose: bool):
    """Run a single task and exit."""
    setup_logging(verbose)

    config = ExecutorConfig.from_env()
    agent = Agent(config)

    # Parse input files
    files = {}
    for fpath in input_files:
        p = Path(fpath)
        if p.exists():
            files[p.name] = p
        else:
            print(f"Warning: input file not found: {fpath}", file=sys.stderr)

    result = agent.run(
        task=task,
        input_files=files if files else None,
        output_dir=Path(output_dir),
    )

    # Print results
    print("\n" + "=" * 60)
    print(result.response)
    print("=" * 60)
    print(f"\nTurns: {result.turns}")
    print(f"Skills used: {', '.join(result.skills_used) or '(none)'}")
    print(f"Tokens: {result.total_input_tokens} in / {result.total_output_tokens} out")
    print(f"Duration: {result.duration_seconds:.1f}s")
    if result.output_files:
        print(f"\nOutput files:")
        for f in result.output_files:
            print(f"  {f}")


def run_interactive(verbose: bool):
    """Run in interactive multi-turn mode."""
    setup_logging(verbose)

    config = ExecutorConfig.from_env()
    agent = MultiTurnAgent(config)
    agent.start_session()

    print("Skills Executor (interactive mode)")
    print(f"Provider: {config.llm.provider.value} / {config.llm.model}")
    print(f"Skills discovered: {len(agent.registry._skills)}")
    print("Type 'quit' to exit, 'skills' to list skills.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "skills":
            print(agent.registry.get_catalog())
            continue

        result = agent.send(user_input)
        print(f"\nAssistant: {result.response}")
        print(f"  [{result.turns} turns, {result.total_input_tokens}+{result.total_output_tokens} tokens]\n")

    agent.end_session()
    print("Session ended.")


def main():
    parser = argparse.ArgumentParser(description="Skills Executor")
    parser.add_argument("task", nargs="?", help="Task to execute (one-shot mode)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--input", "-f", action="append", default=[], help="Input file(s)")
    parser.add_argument("--output-dir", "-o", default="./output", help="Output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    if args.interactive:
        run_interactive(args.verbose)
    elif args.task:
        run_oneshot(args.task, args.input, args.output_dir, args.verbose)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
