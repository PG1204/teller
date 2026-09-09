"""``teller intervene`` — the operator CLI twin of the web console (same file channel)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from teller.hitl.commands import append_command, read_commands, read_intervention, read_state

app = typer.Typer(no_args_is_help=True, help="Operator commands for a paused run (same channel as the web console).")


def _run_dir(run_id: str, runs_dir: Path) -> Path:
    d = runs_dir / run_id
    if not d.exists():
        raise typer.BadParameter(f"no such run: {d}")
    return d


def _pending(run_dir: Path) -> str:
    req = read_intervention(run_dir)
    if not req or req.get("released_at"):
        typer.echo("no pending intervention for this run", err=True)
        raise typer.Exit(code=2)
    return str(req["intervention_id"])


@app.command()
def show(run_id: str, runs_dir: Path = typer.Option(Path("runs"), "--runs-dir")) -> None:
    """Print the pending intervention request, the control state and recent commands."""
    d = _run_dir(run_id, runs_dir)
    typer.echo(json.dumps({"intervention": read_intervention(d), "state": read_state(d), "commands": read_commands(d)[0][-5:]}, indent=2))


@app.command()
def claim(run_id: str, operator: str = typer.Option(os.environ.get("TELLER_OPERATOR", "operator"), "--operator"), runs_dir: Path = typer.Option(Path("runs"), "--runs-dir")) -> None:
    """Take control of the live session (automation stops acting; your actions are recorded)."""
    d = _run_dir(run_id, runs_dir)
    typer.echo(json.dumps(append_command(d, intervention_id=_pending(d), command="claim", operator=operator)))


@app.command()
def resume(
    run_id: str,
    mode: str = typer.Option("retry_step", "--mode", help="retry_step | skip_step | complete"),
    note: str = typer.Option("", "--note"),
    operator: str = typer.Option(os.environ.get("TELLER_OPERATOR", "operator"), "--operator"),
    runs_dir: Path = typer.Option(Path("runs"), "--runs-dir"),
) -> None:
    """Hand control back; the automation verifies the state before continuing."""
    d = _run_dir(run_id, runs_dir)
    typer.echo(json.dumps(append_command(d, intervention_id=_pending(d), command="resume", operator=operator, mode=mode, note=note or None)))


def _simple(command: str):
    def _cmd(run_id: str, note: str = typer.Option("", "--note"), operator: str = typer.Option(os.environ.get("TELLER_OPERATOR", "operator"), "--operator"), runs_dir: Path = typer.Option(Path("runs"), "--runs-dir")) -> None:
        d = _run_dir(run_id, runs_dir)
        typer.echo(json.dumps(append_command(d, intervention_id=_pending(d), command=command, operator=operator, note=note or None)))

    _cmd.__name__ = command
    return _cmd


app.command("confirm", help="Approve the pending irreversible step (it will be performed by automation).")(_simple("confirm"))
app.command("decline", help="Refuse the pending irreversible step; the run ends as declined.")(_simple("decline"))
app.command("abort", help="Stop the run now; the run ends as declined/aborted.")(_simple("abort"))
app.command("dialog-accept", help="Press OK on a browser dialog the automation parked.")(_simple("dialog_accept"))
app.command("dialog-dismiss", help="Press Cancel on a browser dialog the automation parked.")(_simple("dialog_dismiss"))
