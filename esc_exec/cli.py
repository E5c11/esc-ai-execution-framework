from __future__ import annotations

import argparse
from pathlib import Path

from esc_exec.contracts import CONTRACT_FORMATS, validate_contract, validate_contract_set
from esc_exec.indexing import generate_indexes, match_components, validate_indexes
from esc_exec.manifests import generate_gradle_manifests, overall_exit_code, validate_repository
from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeClient, OpenCodeError
from esc_exec.registry import add_route, default_registry_path, read_registry, resolve_route, validate_registry


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
