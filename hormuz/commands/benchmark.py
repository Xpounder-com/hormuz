"""Public terminal interface for user-owned local model benchmarks."""
import json

from ..config import ConfigError
from ..local_history_files import LocalHistoryError


def add_commands(commands):
    benchmark = commands.add_parser("benchmark", help="Benchmark your own tasks on local models")
    operations = benchmark.add_subparsers(dest="benchmark_command", required=True)
    initialize = operations.add_parser("init", help="Create an editable suite with reference checks and targets")
    initialize.add_argument("--output", required=True)
    run = operations.add_parser("run", help="Run a bounded task suite through the local router")
    run.add_argument("--suite", required=True)
    run.add_argument("--models", nargs="+", required=True)
    run.add_argument("--output", required=True, help="New private local scorecard; never overwritten")
    run.add_argument("--actor")
    run.add_argument("--repeats", type=int, default=1, help="1–3 observations per case; never increases distinct-case coverage")
    run.add_argument("--timeout-seconds", type=float, default=30, help="Absolute deadline per request, at most 120 seconds")
    run.add_argument("--deadline-seconds", type=float, default=300, help="Absolute deadline for the run, at most 1800 seconds")
    show = operations.add_parser("show", help="Read a saved scorecard without a running router")
    show.add_argument("--scorecard", required=True)
    show.add_argument("--format", choices=("json", "markdown"), default="json")
    configure = operations.add_parser("configure", help="Stage opt-in scorecard routing in a new configuration")
    configure.add_argument("--scorecard", required=True)
    configure.add_argument("--output", required=True, help="New configuration beside the current configuration")
    configure.add_argument("--dry-run", action="store_true")


def run(args):
    from ..model_benchmark import (BenchmarkError, configure_routing, format_scorecard,
                                  initialize_suite, read_scorecard, run_benchmark)
    try:
        if args.benchmark_command == "init":
            result = initialize_suite(args.output)
        elif args.benchmark_command == "show":
            result = read_scorecard(args.scorecard)
            if args.format == "markdown":
                print(format_scorecard(result))
                return 0
        elif args.benchmark_command == "configure":
            result = configure_routing(args)
        else:
            result = run_benchmark(args)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (BenchmarkError, LocalHistoryError, ConfigError, OSError, ValueError) as error:
        code = error.code if isinstance(error, BenchmarkError) else "benchmark_unavailable"
        print(json.dumps({"schema_id": "hormuz.benchmark-error", "schema_version": 1,
                          "error": {"code": code}}))
        return 2
