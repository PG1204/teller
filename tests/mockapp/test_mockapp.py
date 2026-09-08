"""End-to-end tests for the Ledgerline mock console via TestClient."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mockapp import app as ledgerline
from mockapp import chaos, seed

LOGIN = {"txtU": "teller1", "txtP": "Ledger!2026"}


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    seed.reset()
    chaos.REGISTRY.reset()
    yield
    chaos.REGISTRY.reset()


@pytest.fixture
def anon() -> TestClient:
    return TestClient(ledgerline.app, follow_redirects=False)


@pytest.fixture
def client(anon: TestClient) -> TestClient:
    response = anon.post("/login", data=LOGIN)
    assert response.status_code == 302
    return anon


def arm(client: TestClient, mode: str, times: int = 1) -> None:
    response = client.post("/__chaos", json={"mode": mode, "times": times})
    assert response.status_code == 200, response.text


def test_unauthenticated_console_redirects_to_login(anon: TestClient) -> None:
    response = anon.get("/console/home")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    assert anon.get("/").headers["location"] == "/login"


def test_login_and_frameset(anon: TestClient) -> None:
    bad = anon.post("/login", data={"txtU": "teller1", "txtP": "nope"})
    assert bad.status_code == 200
    assert "Invalid User ID or Password." in bad.text
    assert "LLSESSION" not in bad.cookies

    good = anon.post("/login", data=LOGIN)
    assert good.status_code == 302
    assert good.headers["location"] == "/console"
    assert "LLSESSION" in good.cookies

    shell = anon.get("/console")
    assert shell.status_code == 200
    assert 'name="nav"' in shell.text
    assert 'name="main"' in shell.text
    assert "<title>Ledgerline Credit Union - Member Servicing Console</title>" in shell.text

    home = anon.get("/console/home")
    assert "Welcome, teller1" in home.text
    assert "Servicing" in home.text

    nav = anon.get("/console/nav")
    assert "javascript:go('/console/members/search')" in nav.text


def test_search_results(client: TestClient) -> None:
    page = client.get("/console/members/search")
    assert "Member Search" in page.text
    assert 'name="txtF1"' in page.text
    assert 'type="image" name="cmdGo" alt="Go"' in page.text

    hit = client.get("/console/members/results", params={"txtF1": "10001", "txtF2": ""})
    assert "Search Results" in hit.text
    assert "Dana Whitfield" in hit.text
    assert "onclick=\"location.href='/console/members/10001'\"" in hit.text

    miss = client.get("/console/members/results", params={"txtF1": "20002", "txtF2": ""})
    assert "No members matched your search." in miss.text

    prefix = client.get("/console/members/results", params={"txtF1": "", "txtF2": "nat"})
    assert "Priya Natarajan" in prefix.text
    assert "Dana Whitfield" not in prefix.text


def test_member_detail(client: TestClient) -> None:
    detail = client.get("/console/members/10001")
    assert detail.status_code == 200
    for needle in ("Member Detail", "Share Savings", "0004411982", "$2,431.17", "***-**-4821"):
        assert needle in detail.text
    assert 'class="ssn"' in detail.text
    assert 'href="/console/members/10001/subaccounts/new"' in detail.text

    forbidden = client.get("/console/members/30003")
    assert forbidden.status_code == 403
    assert "ERR-4031" in forbidden.text

    missing = client.get("/console/members/99999")
    assert missing.status_code == 404
    assert "ERR-4041" in missing.text


def test_subaccount_validation_and_success(client: TestClient) -> None:
    url = "/console/members/10001/subaccounts/new"
    form = client.get(url)
    assert "Open Sub-Account" in form.text
    assert "onsubmit=\"return confirm('Open this sub-account?')\"" in form.text

    empty = client.post(url, data={"sel3": "Share Savings", "txtF7": "", "txtF8": "10"})
    assert "Nickname is required" in empty.text

    too_big = client.post(url, data={"sel3": "Share Savings", "txtF7": "Rainy", "txtF8": "50000"})
    assert "Initial deposit must be between 0.00 and 10,000.00" in too_big.text

    dupe = client.post(url, data={"sel3": "Holiday Club", "txtF7": "vacation", "txtF8": "25"})
    assert "Nickname already in use" in dupe.text

    ok = client.post(url, data={"sel3": "Money Market", "txtF7": "Rainy Day", "txtF8": "250.50"})
    assert ok.status_code == 302
    location = ok.headers["location"]
    assert re.fullmatch(r"/console/members/10001/subaccounts/\d{10}/confirmation", location)

    confirmation = client.get(location)
    assert confirmation.status_code == 200
    assert "Sub-Account Opened" in confirmation.text
    assert "CNF-" in confirmation.text
    assert "Rainy Day" in confirmation.text
    assert "$250.50" in confirmation.text

    detail = client.get("/console/members/10001")
    assert "Money Market" in detail.text


def test_chaos_validation_error_is_one_shot(client: TestClient) -> None:
    url = "/console/members/10002/subaccounts/new"
    arm(client, "validation_error")
    data = {"sel3": "Share Savings", "txtF7": "Travel", "txtF8": "10"}
    first = client.post(url, data=data)
    assert "Unable to save. Please correct the highlighted fields." in first.text
    second = client.post(url, data=data)
    assert second.status_code == 302


def test_chaos_app_error_and_unknown_interstitial(client: TestClient) -> None:
    arm(client, "app_error")
    broken = client.get("/console/home")
    assert broken.status_code == 500
    assert "ORA-00600" in broken.text
    assert client.get("/console/home").status_code == 200

    arm(client, "interstitial_unknown")
    gate = client.get("/console/members/search")
    assert gate.status_code == 200
    assert "Attestation Required" in gate.text
    assert 'name="cmdAttest" value="I attest"' in gate.text
    assert 'name="ret" value="/console/members/search"' in gate.text

    attested = client.post("/console/attest", data={"ret": "/console/members/search"})
    assert attested.status_code == 302
    assert attested.headers["location"] == "/console/members/search"
    assert "Member Search" in client.get("/console/members/search").text


def test_chaos_known_interstitial_and_dialogs(client: TestClient) -> None:
    arm(client, "interstitial_known")
    gate = client.get("/console/members/10001")
    assert "Compliance Notice" in gate.text
    assert 'name="cmdAck" value="Acknowledge"' in gate.text
    acked = client.post("/console/notice", data={"ret": "/console/members/10001"})
    assert acked.headers["location"] == "/console/members/10001"

    arm(client, "dialog_known")
    assert "alert('Your session will expire in 5 minutes')" in client.get("/console/home").text
    assert "alert(" not in client.get("/console/home").text

    arm(client, "dialog_unknown", times=2)
    assert "Ledgerline notice #1: continue with pending batch?" in client.get("/console/home").text
    assert "Ledgerline notice #2: continue with pending batch?" in client.get("/console/home").text
    assert "confirm(" not in client.get("/console/home").text


def test_chaos_permission_denied(client: TestClient) -> None:
    arm(client, "permission_denied")
    assert client.get("/console/members/10001").status_code == 403
    assert client.get("/console/members/10001").status_code == 200


def test_chaos_expire_session(client: TestClient) -> None:
    arm(client, "expire_session")
    expired = client.get("/console/home")
    assert expired.status_code == 302
    assert expired.headers["location"] == "/login?reason=expired"

    login = client.get("/login?reason=expired")
    assert login.status_code == 200
    assert "Your session has expired" in login.text
    assert client.get("/console/home").headers["location"] == "/login"


def test_chaos_slow_then_real_page(client: TestClient) -> None:
    arm(client, "slow")
    first = client.get("/console/members/search")
    assert first.status_code == 200
    assert "Loading, please wait" in first.text
    assert '<meta http-equiv="refresh" content="3">' in first.text
    second = client.get("/console/members/search")
    assert "Member Search" in second.text
    assert "Loading, please wait" not in second.text


def test_chaos_blank_page(client: TestClient) -> None:
    arm(client, "blank_page")
    blank = client.get("/console/home")
    assert blank.status_code == 200
    assert blank.text.strip() == "<html><body></body></html>"
    assert "Welcome" in client.get("/console/home").text


def test_chaos_registry_json_and_reset(client: TestClient) -> None:
    arm(client, "slow", times=3)
    status = client.get("/__chaos")
    assert status.status_code == 200
    assert status.json()["armed"] == {"slow": 3}

    bad = client.post("/__chaos", json={"mode": "meteor_strike", "times": 1})
    assert bad.status_code == 400

    cleared = client.post("/__chaos/reset")
    assert cleared.json()["armed"] == {}
    assert client.get("/__chaos").json()["armed"] == {}
    assert client.get("/console/home").status_code == 200


def test_post_transaction_adjusts_balance(client: TestClient) -> None:
    url = "/console/members/10001/transactions/new"
    form = client.get(url)
    assert "Post Transaction" in form.text
    assert 'name="cmdPost" value="Post"' in form.text
    posted = client.post(url, data={"txtF9": "100.00", "sel4": "Deposit", "sel5": "0004411982"})
    assert "Transaction Posted" in posted.text
    assert "REF-" in posted.text
    assert "$2,531.17" in posted.text


def test_no_savings_member_has_only_checking(client: TestClient) -> None:
    detail = client.get("/console/members/10004")
    assert "Leo Brandt" in detail.text
    assert "Checking" in detail.text
    assert "Share Savings" not in detail.text


def test_idle_timeout_redirects_expired(anon: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    anon.post("/login", data=LOGIN)
    monkeypatch.setenv("LEDGERLINE_IDLE_SECONDS", "0")
    monkeypatch.setattr("mockapp.app.time.time", lambda: 9_999_999_999.0)
    response = anon.get("/console/home")
    assert response.status_code == 302
    assert response.headers["location"] == "/login?reason=expired"
