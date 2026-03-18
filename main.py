import argparse

from src.define_universe import define_universe
from src.run_all_pipelines import run_all_stages


def main():
    parser = argparse.ArgumentParser(
        description="Run SpotyBoys data engineering pipeline locally (no Docker)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N tracks for preview download and stage-1 embedding.",
    )
    parser.add_argument(
        "--with-universe",
        action="store_true",
        help="Rebuild universe_metadata.csv and filtered_sessions.csv before running the pipeline.",
    )
    args = parser.parse_args()

    if args.with_universe:
        define_universe()
    run_all_stages(limit=args.limit)


if __name__ == "__main__":
    main()
