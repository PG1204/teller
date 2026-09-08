"""Synthetic in-memory member/account data for the Ledgerline mock console.

Everything here is fictional. State lives in the process and is rebuilt by
``reset()`` (called at import and by the test-suite between tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

PRODUCTS: tuple[str, ...] = ("Share Savings", "Holiday Club", "Money Market")
TRANSACTION_TYPES: tuple[str, ...] = ("Deposit", "Withdrawal", "Fee Reversal")
RESTRICTED_MEMBER = 30003


@dataclass
class Account:
    number: str
    product: str
    balance: Decimal
    opened: date
    nickname: str | None = None
    confirmation_id: str | None = None
    initial_deposit: Decimal | None = None


@dataclass
class Member:
    number: int
    first_name: str
    last_name: str
    status: str
    ssn_last4: str
    accounts: list[Account] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def restricted(self) -> bool:
        return self.number == RESTRICTED_MEMBER

    def find_account(self, number: str) -> Account | None:
        return next((a for a in self.accounts if a.number == number), None)

    def has_nickname(self, nickname: str) -> bool:
        wanted = nickname.strip().casefold()
        return any((a.nickname or "").casefold() == wanted for a in self.accounts)


@dataclass
class Store:
    members: dict[int, Member] = field(default_factory=dict)
    next_account: int = 4412000
    next_confirmation: int = 100
    next_reference: int = 5000


STORE = Store()


def _acct(
    number: str, product: str, balance: str, opened: str, nickname: str | None = None
) -> Account:
    return Account(
        number=number,
        product=product,
        balance=Decimal(balance),
        opened=date.fromisoformat(opened),
        nickname=nickname,
    )


def _seed_members() -> list[Member]:
    return [
        Member(
            10001,
            "Dana",
            "Whitfield",
            "Active",
            "4821",
            [
                _acct("0004411982", "Share Savings", "2431.17", "2014-03-02"),
                _acct("0004411983", "Checking", "812.40", "2014-03-02"),
                _acct("0004411990", "Holiday Club", "150.00", "2021-11-15", "Vacation"),
            ],
        ),
        Member(
            10002,
            "Marcus",
            "Obi",
            "Active",
            "7730",
            [
                _acct("0004412101", "Share Savings", "5210.55", "2009-06-18"),
                _acct("0004412102", "Checking", "1444.02", "2009-06-18"),
            ],
        ),
        Member(
            10003,
            "Priya",
            "Natarajan",
            "Active",
            "1196",
            [
                _acct("0004412201", "Share Savings", "980.00", "2018-01-09"),
                _acct("0004412202", "Money Market", "12500.00", "2019-04-22"),
            ],
        ),
        Member(
            10004,
            "Leo",
            "Brandt",
            "Active",
            "3358",
            [_acct("0004412301", "Checking", "305.19", "2022-08-30")],
        ),
        Member(RESTRICTED_MEMBER, "Restricted", "Member", "Active", "0000", []),
    ]


def reset() -> None:
    """Rebuild the store from the canonical seed."""
    STORE.members = {m.number: m for m in _seed_members()}
    STORE.next_account = 4412000
    STORE.next_confirmation = 100
    STORE.next_reference = 5000


def get_member(number: int) -> Member | None:
    return STORE.members.get(number)


def search(member_number: str, last_name: str) -> list[Member]:
    """Exact match on member number OR case-insensitive last-name prefix."""
    number = member_number.strip()
    prefix = last_name.strip().casefold()
    found: list[Member] = []
    if number.isdigit() and int(number) in STORE.members:
        found.append(STORE.members[int(number)])
    if prefix:
        for member in STORE.members.values():
            if member.last_name.casefold().startswith(prefix) and member not in found:
                found.append(member)
    return sorted(found, key=lambda m: m.number)


def _next_account_number() -> str:
    STORE.next_account += 1
    return f"{STORE.next_account:010d}"


def _next_confirmation_id() -> str:
    STORE.next_confirmation += 1
    return f"CNF-{STORE.next_confirmation:06d}"


def _next_reference() -> str:
    STORE.next_reference += 1
    return f"REF-{STORE.next_reference:07d}"


def open_subaccount(member: Member, product: str, nickname: str, deposit: Decimal) -> Account:
    account = Account(
        number=_next_account_number(),
        product=product,
        balance=deposit,
        opened=date.today(),
        nickname=nickname.strip(),
        confirmation_id=_next_confirmation_id(),
        initial_deposit=deposit,
    )
    member.accounts.append(account)
    return account


def post_transaction(account: Account, kind: str, amount: Decimal) -> str:
    """Apply a posting to the balance and return its reference number."""
    if kind == "Withdrawal":
        account.balance -= amount
    else:
        account.balance += amount
    return _next_reference()


def money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"${value:,.2f}"


reset()
