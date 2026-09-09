"""Ledgerline Credit Union - Member Servicing Console (mock automation target).

Run with ``uvicorn mockapp.app:app --port 8600``. Deliberately legacy: HTML 4.01
frameset, table layouts, cryptic control names, cookie session with idle timeout.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mockapp import chaos, seed
from mockapp.seed import Account, Member

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TITLE = "Ledgerline Credit Union - Member Servicing Console"
COOKIE_NAME = "LLSESSION"
EXPIRED_MESSAGE = "Your session has expired. Please sign in again."
INVALID_LOGIN = "Invalid User ID or Password."
DEPOSIT_RANGE_ERROR = "Initial deposit must be between 0.00 and 10,000.00"
MAX_DEPOSIT = Decimal("10000.00")
# Pages that are never replaced by an interstitial/slow/blank fault or given a dialog:
# the frameset shell and the nav frame (faults must land in the ``main`` frame).
# Faults never land on the frameset shell, the nav frame or the landing page: a fault armed before
# a run fires on the first servicing screen the automation opens, not on the post-login load.
SHELL_PATHS: frozenset[str] = frozenset({"/console", "/console/nav", "/console/home"})
# Interstitial pages are not themselves interrupted by another interstitial.
INTERSTITIAL_EXEMPT: frozenset[str] = SHELL_PATHS | {"/console/notice", "/console/attest"}
# 1x1 transparent GIF, rendered at 60x22 via width/height attributes.
GO_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00"
    b"\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

_SESSION_KEY: bytes = secrets.token_bytes(32)


# --------------------------------------------------------------------------- session


@dataclass(frozen=True)
class Session:
    user: str
    last_seen: int

    def expired(self) -> bool:
        return time.time() - self.last_seen > idle_seconds()


def idle_seconds() -> int:
    return int(os.environ.get("LEDGERLINE_IDLE_SECONDS", "600"))


def credentials() -> tuple[str, str]:
    return (
        os.environ.get("LEDGERLINE_USER", "teller1"),
        os.environ.get("LEDGERLINE_PASS", "Ledger!2026"),
    )


def _sign(payload: bytes) -> str:
    return hmac.new(_SESSION_KEY, payload, hashlib.sha256).hexdigest()


def encode_session(user: str) -> str:
    payload = f"{user}|{int(time.time())}".encode()
    return base64.urlsafe_b64encode(payload).decode() + "." + _sign(payload)


def decode_session(value: str | None) -> Session | None:
    """Return the session carried by the cookie if its signature is valid."""
    if not value or "." not in value:
        return None
    body, sig = value.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(body.encode())
    except (ValueError, binascii.Error):
        return None
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    user, _, stamp = payload.decode(errors="replace").partition("|")
    if not user or not stamp.isdigit():
        return None
    return Session(user=user, last_seen=int(stamp))


def session_of(request: Request) -> Session | None:
    return decode_session(request.cookies.get(COOKIE_NAME))


def _expired_redirect() -> Response:
    response = RedirectResponse("/login?reason=expired", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# --------------------------------------------------------------------------- rendering


def _ensure_static() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    gif = STATIC_DIR / "go.gif"
    if not gif.exists():
        gif.write_bytes(GO_GIF)


_ensure_static()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["money"] = seed.money
templates.env.filters["legacy_date"] = lambda d: d.strftime("%m/%d/%Y")

app = FastAPI(title=TITLE, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(chaos.router)


def _dialog_for(request: Request) -> str:
    path = request.url.path
    if not path.startswith("/console") or path in SHELL_PATHS:
        return ""
    return chaos.REGISTRY.next_dialog_script()


def render(request: Request, template: str, status: int = 200, **context: Any) -> Response:
    context.setdefault("title", TITLE)
    context.setdefault("user", getattr(request.state, "user", None))
    context["dialog_script"] = _dialog_for(request)
    return templates.TemplateResponse(request, template, context, status_code=status)


def requested_url(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


def safe_return_url(ret: str) -> str:
    """Only allow same-app console paths as post-interstitial destinations."""
    if ret.startswith("/console") and not ret.startswith("//"):
        return ret
    return "/console/home"


class PageError(Exception):
    def __init__(self, status: int, template: str) -> None:
        super().__init__(template)
        self.status = status
        self.template = template


@app.exception_handler(PageError)
async def page_error_handler(request: Request, exc: PageError) -> Response:
    return render(request, exc.template, status=exc.status)


# --------------------------------------------------------------------------- middleware


def _chaos_response(request: Request) -> Response | None:
    """Fault to serve instead of the real page, if one is armed."""
    if request.url.path in SHELL_PATHS:
        return None
    if chaos.consume("app_error"):
        return render(request, "error500.html", status=500)
    if request.method != "GET":
        return None
    ret = requested_url(request)
    if request.url.path not in INTERSTITIAL_EXEMPT:
        if chaos.consume("interstitial_known"):
            return render(request, "notice.html", ret=ret)
        if chaos.consume("interstitial_unknown"):
            return render(request, "attest.html", ret=ret)
    if chaos.consume("slow"):
        return render(request, "loading.html", ret=ret)
    if chaos.consume("blank_page"):
        return HTMLResponse(templates.get_template("blank.html").render())
    return None


@app.middleware("http")
async def console_guard(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Session enforcement plus chaos injection for every ``/console*`` request."""
    if not request.url.path.startswith("/console"):
        return await call_next(request)
    session = session_of(request)
    if session is None:
        return RedirectResponse("/login", status_code=302)
    if session.expired():
        return _expired_redirect()
    if request.url.path not in SHELL_PATHS and chaos.consume("expire_session"):
        return _expired_redirect()
    request.state.user = session.user
    response = _chaos_response(request)
    if response is None:
        response = await call_next(request)
    response.set_cookie(COOKIE_NAME, encode_session(session.user), httponly=True)
    return response


# --------------------------------------------------------------------------- auth routes


@app.get("/")
async def root(request: Request) -> Response:
    session = session_of(request)
    target = "/console" if session and not session.expired() else "/login"
    return RedirectResponse(target, status_code=302)


@app.get("/login")
async def login_form(request: Request, reason: str | None = None) -> Response:
    error = EXPIRED_MESSAGE if reason == "expired" else None
    return render(request, "login.html", error=error, user_id="")


@app.post("/login")
async def login_submit(
    request: Request,
    user_id: Annotated[str, Form(alias="txtU")] = "",
    password: Annotated[str, Form(alias="txtP")] = "",
) -> Response:
    user, secret = credentials()
    ok = hmac.compare_digest(user_id.encode(), user.encode()) and hmac.compare_digest(
        password.encode(), secret.encode()
    )
    if not ok:
        return render(request, "login.html", error=INVALID_LOGIN, user_id=user_id)
    response = RedirectResponse("/console", status_code=302)
    response.set_cookie(COOKIE_NAME, encode_session(user), httponly=True)
    return response


@app.get("/logout")
async def logout() -> Response:
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# --------------------------------------------------------------------------- console shell


@app.get("/console")
async def console(request: Request) -> Response:
    return render(request, "frameset.html")


@app.get("/console/nav")
async def nav(request: Request) -> Response:
    return render(request, "nav.html")


@app.get("/console/home")
async def home(request: Request) -> Response:
    return render(request, "home.html", today=date.today())


@app.get("/console/reports")
async def reports(request: Request) -> Response:
    return render(request, "reports.html")


@app.get("/console/notice")
async def notice(request: Request, ret: str = "/console/home") -> Response:
    return render(request, "notice.html", ret=ret)


@app.post("/console/notice")
async def notice_ack(ret: str = Form("/console/home")) -> Response:
    return RedirectResponse(safe_return_url(ret), status_code=302)


@app.get("/console/attest")
async def attest(request: Request, ret: str = "/console/home") -> Response:
    return render(request, "attest.html", ret=ret)


@app.post("/console/attest")
async def attest_submit(ret: str = Form("/console/home")) -> Response:
    return RedirectResponse(safe_return_url(ret), status_code=302)


# --------------------------------------------------------------------------- members


def load_member(member_id: str) -> Member:
    """Resolve a path segment to a member, raising the legacy 404/403 pages."""
    member = seed.get_member(int(member_id)) if member_id.isdigit() else None
    if member is None:
        raise PageError(404, "notfound.html")
    if member.restricted:
        raise PageError(403, "forbidden.html")
    return member


@app.get("/console/members/search")
async def member_search(request: Request) -> Response:
    return render(request, "search.html")


@app.get("/console/members/results")
async def member_results(
    request: Request,
    member_number: Annotated[str, Query(alias="txtF1")] = "",
    last_name: Annotated[str, Query(alias="txtF2")] = "",
) -> Response:
    members = seed.search(member_number, last_name)
    return render(request, "results.html", members=members)


@app.get("/console/members/{member_id}")
async def member_detail(request: Request, member_id: str) -> Response:
    if chaos.consume("permission_denied"):
        raise PageError(403, "forbidden.html")
    member = load_member(member_id)
    return render(request, "detail.html", member=member)


# --------------------------------------------------------------------------- sub-accounts


def parse_amount(raw: str) -> Decimal | None:
    cleaned = raw.strip().replace(",", "").replace("$", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def validate_subaccount(member: Member, nickname: str, deposit_raw: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not nickname.strip():
        errors["txtF7"] = "Nickname is required"
    elif member.has_nickname(nickname):
        errors["txtF7"] = "Nickname already in use"
    deposit = parse_amount(deposit_raw)
    if deposit is None or deposit < 0 or deposit > MAX_DEPOSIT:
        errors["txtF8"] = DEPOSIT_RANGE_ERROR
    return errors


def _subaccount_page(
    request: Request, member: Member, values: dict[str, str], errors: dict[str, str]
) -> Response:
    return render(
        request,
        "subaccount_new.html",
        member=member,
        products=seed.PRODUCTS,
        values=values,
        errors=errors,
    )


@app.get("/console/members/{member_id}/subaccounts/new")
async def subaccount_form(request: Request, member_id: str) -> Response:
    member = load_member(member_id)
    values = {"sel3": seed.PRODUCTS[0], "txtF7": "", "txtF8": ""}
    return _subaccount_page(request, member, values, {})


@app.post("/console/members/{member_id}/subaccounts/new")
async def subaccount_submit(
    request: Request,
    member_id: str,
    product: Annotated[str, Form(alias="sel3")] = seed.PRODUCTS[0],
    nickname: Annotated[str, Form(alias="txtF7")] = "",
    deposit_raw: Annotated[str, Form(alias="txtF8")] = "",
) -> Response:
    member = load_member(member_id)
    values = {"sel3": product, "txtF7": nickname, "txtF8": deposit_raw}
    errors = validate_subaccount(member, nickname, deposit_raw)
    if product not in seed.PRODUCTS:
        errors["sel3"] = "Select a product"
    if chaos.consume("validation_error"):
        errors["form"] = "Unable to save. Please correct the highlighted fields."
    if errors:
        return _subaccount_page(request, member, values, errors)
    deposit = parse_amount(deposit_raw)
    assert deposit is not None  # validated above
    account = seed.open_subaccount(member, product, nickname, deposit)
    url = f"/console/members/{member.number}/subaccounts/{account.number}/confirmation"
    return RedirectResponse(url, status_code=302)


@app.get("/console/members/{member_id}/subaccounts/{account_number}/confirmation")
async def subaccount_confirmation(
    request: Request, member_id: str, account_number: str
) -> Response:
    member = load_member(member_id)
    account = member.find_account(account_number)
    if account is None or account.confirmation_id is None:
        raise PageError(404, "notfound.html")
    return render(request, "confirmation.html", member=member, account=account)


# --------------------------------------------------------------------------- transactions


def _transaction_page(
    request: Request,
    member: Member,
    values: dict[str, str],
    errors: dict[str, str],
    posted: tuple[str, Account] | None = None,
) -> Response:
    return render(
        request,
        "transaction_new.html",
        member=member,
        types=seed.TRANSACTION_TYPES,
        values=values,
        errors=errors,
        posted=posted,
    )


@app.get("/console/members/{member_id}/transactions/new")
async def transaction_form(request: Request, member_id: str) -> Response:
    member = load_member(member_id)
    values = {"txtF9": "", "sel4": seed.TRANSACTION_TYPES[0], "sel5": ""}
    return _transaction_page(request, member, values, {})


@app.post("/console/members/{member_id}/transactions/new")
async def transaction_submit(
    request: Request,
    member_id: str,
    amount_raw: Annotated[str, Form(alias="txtF9")] = "",
    kind: Annotated[str, Form(alias="sel4")] = seed.TRANSACTION_TYPES[0],
    account_number: Annotated[str, Form(alias="sel5")] = "",
) -> Response:
    member = load_member(member_id)
    values = {"txtF9": amount_raw, "sel4": kind, "sel5": account_number}
    errors: dict[str, str] = {}
    amount = parse_amount(amount_raw)
    if amount is None or amount <= 0:
        errors["txtF9"] = "Amount must be greater than 0.00"
    if kind not in seed.TRANSACTION_TYPES:
        errors["sel4"] = "Select a transaction type"
    account = member.find_account(account_number)
    if account is None:
        errors["sel5"] = "Select an account"
    if errors or amount is None or account is None:
        return _transaction_page(request, member, values, errors)
    reference = seed.post_transaction(account, kind, amount)
    return _transaction_page(request, member, values, {}, posted=(reference, account))
