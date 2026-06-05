"""Command line interface for the Altruist tester."""

import typer

from altruist_tester import __version__

app = typer.Typer(
    help=(
        "Post-assembly burn-in tester for Altruist devices. "
        "Stage 1 focuses on one USB-C serial device."
    ),
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"altruist-tester {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        help="Show the installed tester version.",
        is_eager=True,
    ),
) -> None:
    """Run Altruist tester commands."""


if __name__ == "__main__":
    app()
