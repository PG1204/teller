"""``teller discover`` — LLM-driven discovery of a goal against the live surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from teller.discovery.emit import OutputDecl, ParamDecl

CLASSIFICATIONS = ("none", "pii_low", "pii_high", "secret")
PARSERS = ("string", "currency_usd", "int", "decimal", "regex")
TYPES = ("string", "integer", "decimal", "boolean")


def _check(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise typer.BadParameter(f"{what} must be one of {allowed}, got {value!r}")
    return value


def parse_kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        if "=" not in it:
            raise typer.BadParameter(f"expected name=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_output_decl(items: list[str]) -> dict[str, OutputDecl]:
    """name[:type[:classification[:parse]]] e.g. savings_balance:decimal:pii_low:currency_usd"""
    out: dict[str, OutputDecl] = {}
    for it in items:
        bits = it.split(":")
        name = bits[0]
        decl = OutputDecl()
        if len(bits) > 1 and bits[1]:
            decl.type = _check(bits[1], TYPES, "output type")
        if len(bits) > 2 and bits[2]:
            decl.classification = _check(bits[2], CLASSIFICATIONS, "output classification")  # type: ignore[assignment]
        if len(bits) > 3 and bits[3]:
            decl.parse = _check(bits[3], PARSERS, "output parser")
        decl.description = name.replace("_", " ")
        out[name] = decl
    return out


def parse_param_decl(items: list[str]) -> dict[str, ParamDecl]:
    """name[:type[:classification[:pattern]]] e.g. member_id:string:pii_low:^[0-9]{5}$"""
    out: dict[str, ParamDecl] = {}
    for it in items:
        bits = it.split(":", 3)
        decl = ParamDecl()
        if len(bits) > 1 and bits[1]:
            decl.type = _check(bits[1], TYPES, "param type")
        if len(bits) > 2 and bits[2]:
            decl.classification = _check(bits[2], CLASSIFICATIONS, "param classification")  # type: ignore[assignment]
        if len(bits) > 3 and bits[3]:
            decl.pattern = bits[3]
        out[bits[0]] = decl
    return out


def discover(
    goal: str = typer.Option(..., "--goal", help="Natural-language goal for the target app."),
    tenant: str = typer.Option("local", "--tenant"),
    param: list[str] = typer.Option([], "--param", help="name=value (repeatable)."),
    param_decl: list[str] = typer.Option([], "--param-decl", help="name[:type[:classification[:pattern]]]"),
    output: list[str] = typer.Option([], "--output", help="name[:type[:classification[:parse]]] (repeatable)."),
    capability_id: str = typer.Option("ledgerline.member.read_savings_balance", "--capability-id"),
    title: str = typer.Option("Read a member's current Share Savings balance", "--title"),
    version: str = typer.Option("1.0.0", "--version"),
    provider: str = typer.Option("gemini", "--provider", help="gemini | anthropic | scripted"),
    model: str | None = typer.Option(None, "--model"),
    profile_name: str = typer.Option("ledgerline-msc", "--profile", help="Vendor profile under apps/."),
    version_range: str | None = typer.Option(None, "--version-range", help="UI version range recorded against (default: tenant app_version major)."),
    cassette: Path | None = typer.Option(None, "--cassette", help="With --provider scripted: replay this recording."),
    strict_cassette: bool = typer.Option(False, "--strict-cassette", help="Fail on observation drift."),
    max_steps: int | None = typer.Option(None, "--max-steps"),
    headed: bool = typer.Option(False, "--headed", help="Show the browser (attended run)."),
    cdp_port: int | None = typer.Option(None, "--cdp-port", help="Expose the live session over CDP."),
    save: bool = typer.Option(True, "--save/--no-save", help="Write the draft to capabilities/."),
    runs_dir: Path = typer.Option(Path("runs"), "--runs-dir"),
) -> None:
    """Run the observe→decide→act loop once with a real model and emit a draft capability."""
    from teller.artifact.store import ArtifactStore
    from teller.discovery.llm import DeciderError, ScriptedDecider, make_decider
    from teller.discovery.loop import DiscoveryConfig, DiscoveryRunner
    from teller.evidence.log import configure_console_logging
    from teller.policy.model import load_policy

    configure_console_logging()
    root = Path.cwd()
    store = ArtifactStore(root)
    t = store.tenant(tenant)
    profile = store.profile(profile_name)
    policy = load_policy(root / t.policy)
    params = parse_kv(param)
    cfg = DiscoveryConfig(
        goal=goal, params=params, tenant=t, profile=profile, policy=policy,
        capability_id=capability_id, title=title, version=version,
        param_decls=parse_param_decl(param_decl) or {k: ParamDecl(classification="pii_low") for k in params},
        output_decls=parse_output_decl(output),
        max_steps=max_steps or policy.max_steps, max_seconds=policy.max_run_seconds,
        headed=headed, runs_dir=runs_dir, save_to=(root / "capabilities") if save else None, cdp_port=cdp_port,
        version_range=version_range or (f">={t.app_version} <{int(str(t.app_version).split('.')[0]) + 1}" if t.app_version else "*"),
    )
    try:
        if provider == "scripted":
            if cassette is None:
                raise typer.BadParameter("--cassette is required with --provider scripted")
            decider = ScriptedDecider.from_file(str(cassette), strict=strict_cassette)
        else:
            decider = make_decider(provider, model)
    except DeciderError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from e

    from teller.hitl.controller import discovery_escalator

    runner = DiscoveryRunner(cfg, decider, escalator=discovery_escalator(headed=headed))
    report = runner.run()
    typer.echo(json.dumps({
        "status": report.status, "run_id": report.run_id, "evidence_dir": str(report.run_dir),
        "turns": report.turns, "outputs": runner.redactor.masked_params_raw(report.outputs, cfg.output_decls) if report.status != "success" else report.result.outputs,
        "artifact": str(report.artifact_path) if report.artifact_path else None, "reason": report.reason or None,
    }, indent=2))
    from teller.replay.result import EXIT_CODES, Status

    sys.exit(EXIT_CODES[Status(report.status)])
