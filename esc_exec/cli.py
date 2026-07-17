from __future__ import annotations

import argparse
from pathlib import Path

from esc_exec.manifests import generate_gradle_manifests, overall_exit_code, validate_repository
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
    return parser


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

    if args.manifest_command == "generate":
        try:
            paths = generate_gradle_manifests(args.repository)
        except (OSError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        for path in paths:
            print(f"GENERATED  {path}")
        return 0

    results = validate_repository(args.repository)
    for result in results:
        _print_result(result)
    return overall_exit_code(results)
