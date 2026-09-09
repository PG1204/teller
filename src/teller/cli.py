"""``teller`` command line.

Thin: every command parses arguments, loads config, and calls into a package. No logic here.
Subcommands land phase by phase (see docs/PLAN.md §12):

    schema export        P0   JSON Schema files for the artifact, profile, tenant, policy, result
    chaos arm|reset|show P0   arm one-shot faults in the mock console
    discover             P2   LLM-driven discovery run -> draft capability + evidence
    replay               P3   deterministic, model-free execution of a capability
    intervene            P4   operator CLI twin for the handoff channel
    policy check         P3   static verification of an artifact against a policy
    evidence export      P3   curate a run directory into /evidence/<name>
    approve              P6   stretch: draft -> approved
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

app = typer.Typer(no_args_is_help=True, add_completion=False, rich_markup_mode="markdown")
schema_app = typer.Typer(no_args_is_help=True, help="Export/inspect JSON Schemas.")
chaos_app = typer.Typer(no_args_is_help=True, help="Arm one-shot faults in the mock console.")
app.add_typer(schema_app, name="schema")
app.add_typer(chaos_app, name="chaos")

from teller.discovery.cli import app as discover_app  # noqa: E402

app.add_typer(discover_app, name="discover", help="LLM-driven discovery run -> draft capability + evidence.")

load_dotenv()

REPO = Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    """The repo root: cwd if it looks like the project, else the package's parent."""
    cwd = Path.cwd()
    return cwd if (cwd / "tenants").exists() else REPO


# --------------------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------------------


@schema_app.command("export")
def schema_export(
    to: Path = typer.Option(Path("schema"), "--to", help="Output directory."),
) -> None:
    """Write the JSON Schemas generated from the pydantic models."""
    from teller.artifact.model import AppProfile, Capability, Tenant
    from teller.policy.model import Policy
    from teller.replay.result import ResultEnvelope

    to.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in {
        "capability": Capability,
        "app_profile": AppProfile,
        "tenant": Tenant,
        "policy": Policy,
        "result": ResultEnvelope,
    }.items():
        path = to / f"{name}.schema.json"
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://github.com/teller/schema/{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    for p in written:
        typer.echo(f"wrote {p}")


@schema_app.command("validate")
def schema_validate(
    path: Path = typer.Argument(..., help="A capability YAML file."),
    tenant: str | None = typer.Option(None, "--tenant", help="Apply this tenant's overrides."),
) -> None:
    """Load (and merge) a capability and print its summary, or the validation error."""
    from teller.artifact.store import ArtifactError, ArtifactStore

    store = ArtifactStore(_repo_root())
    try:
        cap = store.load(path, tenant)
    except ArtifactError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from e
    typer.echo(
        f"OK {cap.file_stem()} status={cap.capability.status} risk={cap.capability.risk_class} "
        f"steps={len(cap.steps)} params={list(cap.params)} outputs={list(cap.outputs)} "
        f"hash={cap.content_hash()[:12]}"
    )


# --------------------------------------------------------------------------------------
# chaos
# --------------------------------------------------------------------------------------


def _chaos_base(tenant: str) -> str:
    from teller.artifact.store import ArtifactStore

    t = ArtifactStore(_repo_root()).tenant(tenant)
    return t.base_url.rstrip("/") + "/__chaos"


@chaos_app.command("arm")
def chaos_arm(
    mode: str = typer.Argument(..., help="e.g. app_error, interstitial_known, slow"),
    times: int = typer.Option(1, "--times", min=1),
    tenant: str = typer.Option("local", "--tenant"),
) -> None:
    """Arm a one-shot fault in the mock console (bypasses the automation's policy on purpose)."""
    import httpx

    r = httpx.post(_chaos_base(tenant), json={"mode": mode, "times": times}, timeout=5)
    r.raise_for_status()
    typer.echo(json.dumps(r.json()))


@chaos_app.command("reset")
def chaos_reset(tenant: str = typer.Option("local", "--tenant")) -> None:
    import httpx

    r = httpx.post(_chaos_base(tenant) + "/reset", timeout=5)
    r.raise_for_status()
    typer.echo(json.dumps(r.json()))


@chaos_app.command("show")
def chaos_show(tenant: str = typer.Option("local", "--tenant")) -> None:
    import httpx

    r = httpx.get(_chaos_base(tenant), timeout=5)
    r.raise_for_status()
    typer.echo(json.dumps(r.json(), indent=2))


# --------------------------------------------------------------------------------------
# placeholders that later phases replace (kept so `--help` documents the full surface)
# --------------------------------------------------------------------------------------


@app.command()
def version() -> None:
    from teller import __version__

    typer.echo(__version__)


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("interrupted", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()

_ = os  # reserved for env-driven options added in later phases
