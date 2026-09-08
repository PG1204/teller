"""One-shot fault injection for the mock console.

``POST /__chaos {"mode": ..., "times": N}`` arms a mode; every time the fault
fires it consumes one "time" and is disarmed at zero. The ``/__chaos`` prefix
is deliberately separate from ``/console`` so the automation policy can deny it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

MODES: frozenset[str] = frozenset(
    {
        "validation_error",
        "permission_denied",
        "interstitial_known",
        "interstitial_unknown",
        "dialog_known",
        "dialog_unknown",
        "expire_session",
        "slow",
        "app_error",
        "blank_page",
    }
)

KNOWN_DIALOG = "<script>alert('Your session will expire in 5 minutes');</script>"


@dataclass
class ChaosRegistry:
    armed: dict[str, int] = field(default_factory=dict)
    dialog_counter: int = 0

    def arm(self, mode: str, times: int) -> None:
        if times <= 0:
            self.armed.pop(mode, None)
        else:
            self.armed[mode] = times

    def consume(self, mode: str) -> bool:
        """Return True (and decrement) if ``mode`` is armed."""
        remaining = self.armed.get(mode, 0)
        if remaining <= 0:
            return False
        if remaining == 1:
            del self.armed[mode]
        else:
            self.armed[mode] = remaining - 1
        return True

    def snapshot(self) -> dict[str, object]:
        return {"armed": dict(self.armed), "dialog_counter": self.dialog_counter}

    def reset(self) -> None:
        self.armed.clear()
        self.dialog_counter = 0

    def next_dialog_script(self) -> str:
        """Script tag to inject into the next page render, or ''."""
        if self.consume("dialog_known"):
            return KNOWN_DIALOG
        if self.consume("dialog_unknown"):
            self.dialog_counter += 1
            text = f"Ledgerline notice #{self.dialog_counter}: continue with pending batch?"
            return f"<script>confirm('{text}');</script>"
        return ""


REGISTRY = ChaosRegistry()


def consume(mode: str) -> bool:
    return REGISTRY.consume(mode)


class ArmRequest(BaseModel):
    mode: str
    times: int = Field(default=1, ge=0, le=1000)


router = APIRouter(prefix="/__chaos", tags=["chaos"])


@router.post("")
async def arm(body: ArmRequest) -> JSONResponse:
    if body.mode not in MODES:
        return JSONResponse(
            {"error": f"unknown mode '{body.mode}'", "modes": sorted(MODES)},
            status_code=400,
        )
    REGISTRY.arm(body.mode, body.times)
    return JSONResponse(REGISTRY.snapshot())


@router.get("")
async def status() -> JSONResponse:
    return JSONResponse(REGISTRY.snapshot())


@router.post("/reset")
async def reset() -> JSONResponse:
    REGISTRY.reset()
    return JSONResponse(REGISTRY.snapshot())
