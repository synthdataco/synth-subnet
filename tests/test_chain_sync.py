from dataclasses import dataclass
from typing import List

from synth.miner.chain_sync import (
    UfwState,
    compute_desired_allowlist,
    compute_diff,
    parse_ufw_status,
)


@dataclass
class _FakeAxon:
    ip: str


@dataclass
class _FakeMetagraph:
    n: int
    validator_permit: List[bool]
    S: List[float]
    axons: List[_FakeAxon]


def _mg(rows):
    permits = [r[0] for r in rows]
    stakes = [r[1] for r in rows]
    axons = [_FakeAxon(ip=r[2]) for r in rows]
    return _FakeMetagraph(
        n=len(rows), validator_permit=permits, S=stakes, axons=axons
    )


def test_compute_desired_allowlist_filters_permit_and_stake():
    metagraph = _mg(
        [
            (True, 5000.0, "1.1.1.1"),  # included
            (True, 999.0, "2.2.2.2"),  # excluded: stake below threshold
            (False, 5000.0, "3.3.3.3"),  # excluded: no permit
            (True, 1500.0, "0.0.0.0"),  # excluded: unrouted axon
            (True, 1000.0, "4.4.4.4"),  # included: stake == threshold
        ]
    )
    assert compute_desired_allowlist(metagraph, min_stake=1000) == {
        "1.1.1.1",
        "4.4.4.4",
    }


def test_compute_desired_allowlist_dedupes_repeated_ips():
    metagraph = _mg(
        [
            (True, 2000.0, "1.1.1.1"),
            (True, 3000.0, "1.1.1.1"),
        ]
    )
    assert compute_desired_allowlist(metagraph, min_stake=1000) == {"1.1.1.1"}


_BLANKET_STATUS = """Status: active

To                         Action      From
--                         ------      ----
OpenSSH                    ALLOW       Anywhere
8091/tcp                   ALLOW       Anywhere
OpenSSH (v6)               ALLOW       Anywhere (v6)
8091/tcp (v6)              ALLOW       Anywhere (v6)
"""

_PER_IP_STATUS = """Status: active

To                         Action      From
--                         ------      ----
OpenSSH                    ALLOW       Anywhere
8091/tcp                   ALLOW       1.1.1.1
8091/tcp                   ALLOW       2.2.2.2
9000/tcp                   ALLOW       9.9.9.9
"""


def test_parse_ufw_status_detects_blanket():
    state = parse_ufw_status(_BLANKET_STATUS, axon_port=8091)
    assert state.has_blanket is True
    assert state.current == set()


def test_parse_ufw_status_extracts_per_ip_only_for_target_port():
    state = parse_ufw_status(_PER_IP_STATUS, axon_port=8091)
    assert state.has_blanket is False
    assert state.current == {"1.1.1.1", "2.2.2.2"}


def test_parse_ufw_status_ignores_ssh_rules():
    state = parse_ufw_status(_PER_IP_STATUS, axon_port=8091)
    # OpenSSH rule is not surfaced anywhere in current
    assert "Anywhere" not in state.current
    assert all(ip not in state.current for ip in ("9.9.9.9",))


def test_compute_diff_first_run_replaces_blanket():
    desired = {"1.1.1.1", "2.2.2.2"}
    state = UfwState(current=set(), has_blanket=True)
    diff = compute_diff(desired, state)
    assert diff.to_add == ["1.1.1.1", "2.2.2.2"]
    assert diff.to_remove == []
    assert diff.remove_blanket is True


def test_compute_diff_steady_state_is_noop():
    desired = {"1.1.1.1", "2.2.2.2"}
    state = UfwState(current={"1.1.1.1", "2.2.2.2"}, has_blanket=False)
    diff = compute_diff(desired, state)
    assert diff.to_add == []
    assert diff.to_remove == []
    assert diff.remove_blanket is False


def test_compute_diff_adds_and_removes():
    desired = {"1.1.1.1", "3.3.3.3"}
    state = UfwState(current={"1.1.1.1", "2.2.2.2"}, has_blanket=False)
    diff = compute_diff(desired, state)
    assert diff.to_add == ["3.3.3.3"]
    assert diff.to_remove == ["2.2.2.2"]
    assert diff.remove_blanket is False


def test_compute_diff_keeps_blanket_when_desired_empty():
    # Belt-and-suspenders: reconcile_once already short-circuits on an
    # empty desired set, but compute_diff itself must never propose
    # tearing down the blanket without a replacement.
    desired: set = set()
    state = UfwState(current={"1.1.1.1"}, has_blanket=True)
    diff = compute_diff(desired, state)
    assert diff.remove_blanket is False
