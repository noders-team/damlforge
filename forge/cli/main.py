# Copyright 2026 Noders Team
# Author: Artem Bogomaz <artembogomaz@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import sys
from pathlib import Path

import click
from loguru import logger

from forge import __version__
from forge.cli.forge_daml import compile_daml
from forge.cli.forge_dar import build_dar
from forge.cli.forge_json import compile_json
from forge.stubgen import generate_stubs

_TRACEBACK_OPTION = click.option(
    "--traceback", is_flag=True, help="Show a Python traceback on error instead of a short message."
)
_PRETTY_JSON_OPTION = click.option(
    "--pretty-json", is_flag=True, help="Output JSON in pretty (indented) format."
)


class _DefaultCommandGroup(click.Group):
    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """Route a bare path argument to the `daml` command."""
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            args = ["daml", *args]
        return super().resolve_command(ctx, args)


@click.group(cls=_DefaultCommandGroup, invoke_without_command=True)
@click.version_option(__version__, "--version", "-V", prog_name="forge")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """DamlForge: compile .vy sources (Python/Vyper-flavored syntax) into Daml/Canton."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command("daml")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o",
    "output_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to write DAML output to. Defaults next to INPUT_PATH.",
)
@_PRETTY_JSON_OPTION
@click.option(
    "--i/--no-i",
    "ignore_errors",
    default=True,
    help="Keep compiling remaining files after one fails (default: enabled).",
)
@_TRACEBACK_OPTION
def daml_cmd(
    input_path: Path,
    output_dir: Path | None,
    pretty_json: bool,
    ignore_errors: bool,
    traceback: bool,
) -> None:
    """Compile .vy source(s) at INPUT_PATH (a file or a directory) into .daml."""
    if output_dir is not None:
        target = output_dir.resolve()
    elif input_path.is_dir():
        target = input_path
    else:
        target = input_path.parent
    try:
        compile_daml(
            input_path=input_path,
            output_dir=target,
            pretty_json=pretty_json,
            ignore_errors=ignore_errors,
            traceback=traceback,
        )
    except SystemExit as exc:
        sys.exit(exc.code)


@cli.command("dar")
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "-o",
    "output_dar",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to save the built .dar to. Defaults to <input_dir>/<name>-<version>.dar.",
)
@click.option(
    "--dpm",
    "dpm_path",
    default=None,
    help="Path to the dpm executable. Defaults to searching PATH, then ~/.dpm/bin/dpm.",
)
@_TRACEBACK_OPTION
def dar_cmd(
    input_dir: Path, output_dar: Path | None, dpm_path: str | None, traceback: bool
) -> None:
    """Compile the .vy sources of a Daml project at INPUT_DIR and package them into a .dar."""
    target = output_dar.resolve() if output_dar is not None else None
    try:
        dar_path = build_dar(input_dir=input_dir, output_dar=target, dpm_path=dpm_path)
    except Exception as exc:
        if traceback:
            raise
        logger.error(f"Error building .dar: {exc}")
        sys.exit(1)

    logger.info(f"Built .dar: {dar_path}")


@cli.command("stubs")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    help="Directory to write the generated .vy stubs to. Defaults to the current directory.",
)
@click.option(
    "--dpm",
    "dpm_path",
    default=None,
    help="Path to the dpm executable, used when INPUT_PATH is a Daml project directory.",
)
@click.option(
    "--module",
    "modules",
    multiple=True,
    help="Generate only this module, from any package in the DAR (repeatable).",
)
@_TRACEBACK_OPTION
def stubs_cmd(
    input_path: Path,
    output_dir: Path,
    dpm_path: str | None,
    modules: tuple[str, ...],
    traceback: bool,
) -> None:
    """Generate .vy stubs from a .dar file or a Daml project directory at INPUT_PATH."""
    try:
        written, skipped = generate_stubs(input_path, output_dir.resolve(), dpm_path, modules)
    except Exception as exc:
        if traceback:
            raise
        logger.error(f"Error generating stubs: {exc}")
        sys.exit(1)

    for path in written:
        logger.info(f"Wrote stub: {path}")
    for reason in skipped:
        logger.warning(f"Skipped {reason}")


@cli.command("json")
@click.argument(
    "input_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=False
)
@click.option(
    "-o",
    "output_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Filename to save JSON output to. If omitted, prints to stdout.",
)
@_PRETTY_JSON_OPTION
@_TRACEBACK_OPTION
def json_cmd(
    input_file: Path | None, output_file: Path | None, pretty_json: bool, traceback: bool
) -> None:
    """Compile a single .vy file (or stdin, if INPUT_FILE is omitted) to its JSON AST."""
    if input_file is not None:
        input_vy = input_file.read_text()
    else:
        input_vy = sys.stdin.read().strip()

    try:
        result = compile_json(input_vy)
    except Exception as exc:
        if traceback:
            raise
        click.echo(json.dumps({"error": str(exc)}), err=True)
        sys.exit(1)

    indent = 2 if pretty_json else None
    output_json = json.dumps(result, indent=indent, sort_keys=True, default=str)

    if output_file is not None:
        resolved_output = output_file.resolve()
        resolved_output.write_text(output_json)
        click.echo(f"Results saved to {resolved_output}")
    else:
        click.echo(output_json)


if __name__ == "__main__":
    cli()
