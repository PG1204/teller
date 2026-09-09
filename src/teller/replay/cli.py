"""``teller replay`` / ``teller policy check`` / ``teller evidence export``."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import typer

replay_app = typer.Typer(add_completion=False)
policy_app = typer.Typer(no_args_is_help=True, help="Static policy verification.")
evidence_app = typer.Typer(no_args_is_help=True, help="Curate run directories into /evidence.")


def _kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        if "=" not in it:
            raise typer.BadParameter(f"expected name=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@replay_app.callback(invoke_without_command=True)
def replay(
    artifact: Path = typer.Argument(..., help="capabilities/<id>@<version>.yaml"),
    tenant: str = typer.Option("local", "--tenant"),
    param: list[str] = typer.Option([], "--param", help="name=value (repeatable)."),
    headed: bool = typer.Option(False, "--headed", help="Show the browser; enables human handoff."),
    unattended: bool = typer.Option(False, "--unattended", help="Never wait for a human; requires an approved artifact."),
    confirm_step: list[str] = typer.Option([], "--confirm-step", help="Pre-authorise an irreversible step, e.g. s7@1.0.0."),
    emit_full_outputs: bool = typer.Option(False, "--emit-full-outputs", help="Write pii_high outputs unmasked to result.json."),
    runs_dir: Path = typer.Option(Path("runs"), "--runs-dir"),
    cdp_port: int | None = typer.Option(None, "--cdp-port"),
    hitl: str = typer.Option("auto", "--hitl", help="auto | cli | none — how an operator resumes a paused run."),
) -> None:
    """Deterministic, model-free replay of a saved capability with typed params."""
    from teller.evidence.log import configure_console_logging
    from teller.hitl.controller import replay_escalator
    from teller.replay.executor import ReplayConfig, ReplayRunner
    from teller.replay.result import exit_code

    configure_console_logging()
    cfg = ReplayConfig(
        artifact_path=artifact, params=_kv(param), tenant=tenant, root=Path.cwd(), headed=headed,
        unattended=unattended, confirm_steps=set(confirm_step), emit_full_outputs=emit_full_outputs,
        runs_dir=runs_dir, cdp_port=cdp_port,
    )
    runner = ReplayRunner(cfg, escalator=None if unattended else replay_escalator(mode=hitl, headed=headed))
    result = runner.run()
    typer.echo(result.model_dump_json(indent=2, exclude_none=True, include={"status", "run_id", "outputs", "outcome", "failure", "declined", "recoveries", "warnings", "handoffs", "evidence_dir"}))
    sys.exit(exit_code(result))


@policy_app.command("check")
def policy_check(
    artifact: Path = typer.Argument(...),
    tenant: str = typer.Option("local", "--tenant"),
) -> None:
    """Verify an artifact against the tenant's policy before any browser is launched."""
    from teller.artifact.store import ArtifactError, ArtifactStore
    from teller.policy.model import load_policy
    from teller.replay.executor import static_policy_check

    store = ArtifactStore(Path.cwd())
    try:
        t = store.tenant(tenant)
        cap = store.load(artifact, t)
    except ArtifactError as e:
        typer.echo(f"ARTIFACT_INVALID: {e}", err=True)
        raise typer.Exit(code=2) from e
    policy = load_policy(store.root / t.policy)
    problems = static_policy_check(cap, policy, t)
    summary = {
        "capability": cap.file_stem(), "status": cap.capability.status, "risk_class": cap.capability.risk_class,
        "steps": len(cap.steps), "irreversible_steps": [s.id for s in cap.steps if s.risk_class == "irreversible_write"],
        "policy_sha256": policy.sha256(), "problems": problems,
    }
    typer.echo(json.dumps(summary, indent=2))
    raise typer.Exit(code=1 if problems else 0)


@evidence_app.command("export")
def evidence_export(
    run_id: str = typer.Argument(..., help="A run id under runs/."),
    to: Path = typer.Option(..., "--to", help="Destination, e.g. evidence/replay-success"),
    runs_dir: Path = typer.Option(Path("runs"), "--runs-dir"),
    note: str = typer.Option("", "--note", help="One line on what this run demonstrates."),
) -> None:
    """Copy a run into /evidence, re-scrub text files, refuse traces, write an index.md."""
    from teller.evidence.export import export_run

    src = runs_dir / run_id
    if not src.exists():
        typer.echo(f"no such run: {src}", err=True)
        raise typer.Exit(code=2)
    if to.exists():
        shutil.rmtree(to)
    index = export_run(src, to, note=note)
    typer.echo(f"exported {run_id} -> {to} ({index})")
