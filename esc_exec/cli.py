from __future__ import annotations

import argparse
from pathlib import Path

from esc_exec.contracts import CONTRACT_FORMATS, validate_contract, validate_contract_set
from esc_exec.indexing import generate_indexes, match_components, validate_indexes
from esc_exec.manifests import generate_gradle_manifests, overall_exit_code, validate_repository
from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeClient, OpenCodeError
from esc_exec.registry import add_route, default_registry_path, read_registry, resolve_route, validate_registry
from esc_exec.reporting import summarize_junit
from esc_exec.task_context import build_task_context, build_verification_plan, generate_gradle_verification_profile
from esc_exec.architecture import check_architecture, generate_architecture_profile
from esc_exec.checkpoints import create_checkpoint, inspect_checkpoint, update_checkpoint
from esc_exec.dependencies import analyze_impact, generate_dependency_graph, validate_dependency_graph
from esc_exec.measurement import compare_efficiency


def _registry_path(raw: str | None) -> Path:
    return Path(raw).expanduser() if raw else default_registry_path()


def _print_result(result) -> None:
    print(f"{result.state.value:<10} {result.path}")
    for message in result.messages:
        print(f"  - {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="esc-exec")
    parser.add_argument("--registry", help="Override the machine-local route registry path")
    subcommands = parser.add_subparsers(dest="command", required=True)

    route = subcommands.add_parser("route", help="Manage machine-local repository/framework routes")
    route_commands = route.add_subparsers(dest="route_command", required=True)
    route_add = route_commands.add_parser("add")
    route_add.add_argument("kind", choices=("repository", "framework"))
    route_add.add_argument("id")
    route_add.add_argument("path", type=Path)
    route_resolve = route_commands.add_parser("resolve")
    route_resolve.add_argument("kind", choices=("repository", "framework"))
    route_resolve.add_argument("id")
    route_commands.add_parser("list")
    route_commands.add_parser("validate")

    manifest = subcommands.add_parser("manifest", help="Generate and validate repository/component manifests")
    manifest_commands = manifest.add_subparsers(dest="manifest_command", required=True)
    generate = manifest_commands.add_parser("generate")
    generate.add_argument("repository", type=Path)
    validate = manifest_commands.add_parser("validate")
    validate.add_argument("repository", type=Path)

    index = subcommands.add_parser("index", help="Generate, validate, and query JSON routing indexes")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    index_generate = index_commands.add_parser("generate")
    index_generate.add_argument("repository")
    index_validate = index_commands.add_parser("validate")
    index_validate.add_argument("repository")
    index_match = index_commands.add_parser("match")
    index_match.add_argument("repository")
    index_match.add_argument("query", nargs="+")
    index_match.add_argument("--limit", type=int, default=3)

    contract = subcommands.add_parser("contract", help="Validate provider-neutral execution contracts")
    contract_commands = contract.add_subparsers(dest="contract_command", required=True)
    contract_validate = contract_commands.add_parser("validate")
    contract_validate.add_argument("kind", choices=tuple(CONTRACT_FORMATS))
    contract_validate.add_argument("path", type=Path)
    contract_set = contract_commands.add_parser("validate-set")
    contract_set.add_argument("directory", type=Path)

    report = subcommands.add_parser("report", help="Create bounded summaries of retained reports")
    report_commands = report.add_subparsers(dest="report_command", required=True)
    report_summarize = report_commands.add_parser("summarize")
    report_summarize.add_argument("profile", type=Path)
    report_summarize.add_argument("source", type=Path)
    report_summarize.add_argument("output", type=Path)
    report_summarize.add_argument("--full-report-path")

    context = subcommands.add_parser("context", help="Build bounded task-specific routing context")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    context_build = context_commands.add_parser("build")
    context_build.add_argument("repository")
    context_build.add_argument("task", type=Path)
    context_build.add_argument("output", type=Path)
    context_build.add_argument("--max-components", type=int, default=10)
    context_build.add_argument("--max-paths", type=int, default=30)
    context_build.add_argument("--max-references", type=int, default=30)

    verification = subcommands.add_parser("verification", help="Manage progressive verification plans")
    verification_commands = verification.add_subparsers(dest="verification_command", required=True)
    profile = verification_commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_generate = profile_commands.add_parser("generate")
    profile_generate.add_argument("repository")
    profile_generate.add_argument("component")
    verification_plan = verification_commands.add_parser("plan")
    verification_plan.add_argument("repository")
    verification_plan.add_argument("task", type=Path)
    verification_plan.add_argument("output", type=Path)

    architecture = subcommands.add_parser("architecture", help="Run component architecture fitness functions")
    architecture_commands = architecture.add_subparsers(dest="architecture_command", required=True)
    architecture_profile = architecture_commands.add_parser("profile")
    architecture_profile_commands = architecture_profile.add_subparsers(dest="architecture_profile_command", required=True)
    architecture_profile_generate = architecture_profile_commands.add_parser("generate")
    architecture_profile_generate.add_argument("repository")
    architecture_profile_generate.add_argument("component")
    architecture_check = architecture_commands.add_parser("check")
    architecture_check.add_argument("repository")
    architecture_check.add_argument("component")
    architecture_check.add_argument("output", type=Path)

    checkpoint = subcommands.add_parser("checkpoint", help="Create and maintain durable task handoffs")
    checkpoint_commands = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_create = checkpoint_commands.add_parser("create")
    checkpoint_create.add_argument("repository")
    checkpoint_create.add_argument("task", type=Path)
    checkpoint_create.add_argument("--run-id")
    checkpoint_create.add_argument("--status", choices=("active", "blocked", "ready-to-resume"), default="active")
    checkpoint_update = checkpoint_commands.add_parser("update")
    checkpoint_update.add_argument("repository")
    checkpoint_update.add_argument("task_id")
    checkpoint_update.add_argument("--run-id")
    checkpoint_update.add_argument("--status", choices=("active", "blocked", "ready-to-resume"))
    checkpoint_update.add_argument("--clear-blockers", action="store_true")
    checkpoint_inspect = checkpoint_commands.add_parser("inspect")
    checkpoint_inspect.add_argument("repository")
    checkpoint_inspect.add_argument("task_id")
    for command in (checkpoint_create, checkpoint_update):
        command.add_argument("--completed", action="append", default=[])
        command.add_argument("--decision", action="append", default=[])
        command.add_argument("--remaining", action="append", default=[])
        command.add_argument("--blocker", action="append", default=[])
        command.add_argument("--artifact", action="append", default=[])
        command.add_argument("--last-event-sequence", type=int)

    dependency = subcommands.add_parser("dependency", help="Generate component dependencies and analyze consumers")
    dependency_commands = dependency.add_subparsers(dest="dependency_command", required=True)
    dependency_generate = dependency_commands.add_parser("generate")
    dependency_generate.add_argument("repository")
    dependency_validate = dependency_commands.add_parser("validate")
    dependency_validate.add_argument("repository")
    dependency_impact = dependency_commands.add_parser("impact")
    dependency_impact.add_argument("repository")
    dependency_impact.add_argument("components", nargs="+")
    dependency_impact.add_argument("--output", type=Path, required=True)

    measurement = subcommands.add_parser("measurement", help="Compare execution-efficiency evidence")
    measurement_commands = measurement.add_subparsers(dest="measurement_command", required=True)
    measurement_compare = measurement_commands.add_parser("compare")
    measurement_compare.add_argument("--baseline", type=Path, action="append", required=True)
    measurement_compare.add_argument("--candidate", type=Path, action="append", required=True)
    measurement_compare.add_argument("--output", type=Path, required=True)

    opencode = subcommands.add_parser("opencode", help="Run the OpenCode reference adapter")
    opencode_commands = opencode.add_subparsers(dest="opencode_command", required=True)
    opencode_execute = opencode_commands.add_parser("execute")
    opencode_execute.add_argument("task", type=Path)
    opencode_execute.add_argument("workspace", type=Path)
    opencode_execute.add_argument("adapter", type=Path)
    opencode_execute.add_argument("policy", type=Path)
    opencode_execute.add_argument("--server", default="http://127.0.0.1:4097")
    opencode_execute.add_argument("--output", type=Path, default=Path(".execution/runs"))
    opencode_execute.add_argument("--session")
    opencode_fork = opencode_commands.add_parser("fork")
    opencode_fork.add_argument("repository")
    opencode_fork.add_argument("session")
    opencode_fork.add_argument("--server", default="http://127.0.0.1:4097")
    return parser


def _resolve_repository(value: str, registry: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    return resolve_route(registry, "repositories", value)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = _registry_path(args.registry)
    if args.command == "route":
        category = {
            "repository": "repositories",
            "framework": "frameworks",
        }.get(getattr(args, "kind", ""), "")
        if args.route_command == "add":
            if not args.path.expanduser().is_dir():
                print(f"INVALID    Route target is not a directory: {args.path}")
                return 1
            add_route(registry, category, args.id, args.path)
            print(f"REGISTERED {args.kind} `{args.id}` -> {args.path.expanduser().resolve()}")
            return 0
        if args.route_command == "resolve":
            try:
                print(resolve_route(registry, category, args.id))
                return 0
            except (KeyError, FileNotFoundError) as exc:
                print(f"INCOMPLETE {exc.args[0]}")
                return 2
        if args.route_command == "list":
            data = read_registry(registry)
            print(f"Registry: {registry}")
            for kind in ("repositories", "frameworks"):
                print(f"{kind}:")
                for route_id, route in data.get(kind, {}).items():
                    print(f"  {route_id}: {route.get('path')}")
            return 0
        result = validate_registry(registry)
        _print_result(result)
        return result.exit_code

    if args.command == "manifest" and args.manifest_command == "generate":
        try:
            paths = generate_gradle_manifests(args.repository)
        except (OSError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        for path in paths:
            print(f"GENERATED  {path}")
        return 0

    if args.command == "manifest":
        results = validate_repository(args.repository)
        for result in results:
            _print_result(result)
        return overall_exit_code(results)

    if args.command == "contract":
        if args.contract_command == "validate-set":
            results = validate_contract_set(args.directory)
            for result in results:
                _print_result(result)
            return overall_exit_code(results)
        result = validate_contract(args.kind, args.path)
        _print_result(result)
        return result.exit_code

    if args.command == "report":
        try:
            document = summarize_junit(args.source, args.profile, args.output, args.full_report_path)
        except (OSError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        totals = document["totals"]
        print(
            f"{document['verification']['status'].upper():<10} "
            f"tests={totals['tests']} failed={totals['failed']} errors={totals['errors']} "
            f"summary={args.output} full={document['full_report']['path']}"
        )
        return 0

    if args.command in {"context", "verification"}:
        try:
            repository = _resolve_repository(args.repository, registry)
            if args.command == "context":
                document = build_task_context(
                    repository, args.task, args.output,
                    args.max_components, args.max_paths, args.max_references,
                )
                print(
                    f"GENERATED  {args.output} "
                    f"components={len(document['routing']['components'])}"
                )
            elif args.verification_command == "profile":
                print(f"GENERATED  {generate_gradle_verification_profile(repository, args.component)}")
            else:
                document = build_verification_plan(repository, args.task, args.output)
                statuses = ", ".join(f"{gate['id']}={gate['status']}" for gate in document["gates"])
                print(f"GENERATED  {args.output} {statuses}")
            return 0
        except (KeyError, OSError, ValueError, FileNotFoundError) as exc:
            print(f"INCOMPLETE {exc}")
            return 2

    if args.command == "architecture":
        try:
            repository = _resolve_repository(args.repository, registry)
            if args.architecture_command == "profile":
                print(f"GENERATED  {generate_architecture_profile(repository, args.component)}")
                print("INCOMPLETE Author at least one stable architecture rule before checking.")
                return 2
            document = check_architecture(repository, args.component, args.output)
            totals = document["totals"]
            print(
                f"{document['status'].upper():<10} rules={totals['rules']} "
                f"failed={totals['failed']} violations={totals['violations']} report={args.output}"
            )
            return 1 if document["status"] == "failed" else 0
        except (KeyError, OSError, ValueError, FileNotFoundError) as exc:
            print(f"INCOMPLETE {exc}")
            return 2

    if args.command == "checkpoint":
        try:
            repository = _resolve_repository(args.repository, registry)
            if args.checkpoint_command == "inspect":
                print(inspect_checkpoint(repository, args.task_id))
                return 0
            values = {
                "run_id": args.run_id,
                "status": args.status,
                "completed": args.completed,
                "decisions": args.decision,
                "remaining": args.remaining,
                "blockers": args.blocker,
                "artifacts": args.artifact,
                "last_event_sequence": args.last_event_sequence,
            }
            if args.checkpoint_command == "create":
                path = create_checkpoint(repository, args.task, **values)
            else:
                values["clear_blockers"] = args.clear_blockers
                path = update_checkpoint(repository, args.task_id, **values)
            print(f"CHECKPOINT {path}")
            print("COMMIT     Review and commit this checkpoint for durable handoff.")
            return 0
        except (KeyError, OSError, ValueError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1

    if args.command == "dependency":
        try:
            repository = _resolve_repository(args.repository, registry)
            if args.dependency_command == "generate":
                print(f"GENERATED  {generate_dependency_graph(repository)}")
                return 0
            if args.dependency_command == "validate":
                result = validate_dependency_graph(repository)
                _print_result(result)
                return result.exit_code
            document = analyze_impact(repository, args.components, args.output)
            print(
                f"IMPACT     sources={','.join(document['source_components'])} "
                f"consumers={','.join(document['transitive_consumers']) or 'none'} report={args.output}"
            )
            return 0
        except (KeyError, OSError, ValueError, FileNotFoundError) as exc:
            print(f"INCOMPLETE {exc}")
            return 2

    if args.command == "measurement":
        try:
            document = compare_efficiency(args.baseline, args.candidate, args.output)
            print(f"COMPARISON {args.output}")
            for name, dimension in document["dimensions"].items():
                change = dimension["change_percent"]
                rendered = "unavailable" if change is None else f"{change:+.2f}% savings"
                print(f"  {name}: {dimension['status']} ({rendered})")
            return 0
        except (KeyError, OSError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1

    if args.command == "opencode":
        adapter = OpenCodeAdapter(OpenCodeClient(args.server), registry)
        try:
            if args.opencode_command == "fork":
                print(adapter.fork(args.repository, args.session))
            else:
                print(adapter.execute(args.task, args.workspace, args.adapter, args.policy, args.output, args.session))
            return 0
        except (OpenCodeError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"FAILED     {exc}")
            return 1

    try:
        repository = _resolve_repository(args.repository, registry)
    except (KeyError, FileNotFoundError) as exc:
        print(f"INCOMPLETE {exc.args[0]}")
        return 2
    if args.index_command == "generate":
        try:
            paths = generate_indexes(repository)
        except (KeyError, OSError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        for path in paths:
            print(f"GENERATED  {path}")
        return 0
    if args.index_command == "validate":
        results = validate_indexes(repository)
        for result in results:
            _print_result(result)
        return overall_exit_code(results)
    try:
        matches = match_components(repository, " ".join(args.query))[:max(args.limit, 0)]
    except (OSError, ValueError, KeyError) as exc:
        print(f"INVALID    {exc}")
        return 1
    if not matches:
        print("NO_MATCH   No component routing terms matched the query.")
        return 2
    for match in matches:
        print(f"MATCH      {match.component_id} score={match.score}")
        print(f"  index: {match.index}")
        for reason in match.reasons:
            print(f"  reason: {reason}")
        for search_root in match.search_roots:
            print(f"  search: {search_root}")
    return 0
