"""Optional ingestion of external VHFT (Synth Ultra) competition scores.

VHFT is the 10-second BTC-microprice competition, scored OFF-subnet by a separate
scorer that exposes per-uid raw scores over HTTP. When VHFT_SCORES_URL is set, the
validator pulls those scores each scoring cycle and blends them in as a 4th
competition (see forward.calculate_moving_average_and_update_rewards). Disabled —
from_env() returns None — when the env var is unset, so this is inert until
explicitly configured (safe to merge).

Best-effort: any fetch/parse failure returns None and VHFT is simply skipped for
that cycle; the other three competitions still set weights.
"""

import os
import typing

import bittensor as bt
import requests

_DEFAULT_TIMEOUT_S = 10


class VhftScoreProvider:
    def __init__(self, url: str):
        self._url = url
        bt.logging.info(f"VHFT score provider enabled (url: {self._url})")

    @staticmethod
    def from_env() -> typing.Optional["VhftScoreProvider"]:
        """Build from VHFT_SCORES_URL; returns None (disabled) when unset."""
        url = os.getenv("VHFT_SCORES_URL")
        if not url:
            return None
        return VhftScoreProvider(url=url)

    def fetch_scores(self) -> typing.Optional[dict[int, float]]:
        """Return {miner_uid: mean_crps} for VHFT participants only.

        The /v1/scores endpoint returns all 256 uids, with mean_crps=0.0 (and
        weight=0.0) for non-participants. Those are EXCLUDED here: 0.0 would read
        as a *perfect* score to the lower-is-better softmax downstream, wrongly
        rewarding miners that never competed in VHFT. We keep only uids the scorer
        gave positive weight (i.e. actually-scored participants) and return their
        raw mean_crps (the blend applies its own softmax, so pass the raw score,
        not the pre-normalized weight).

        Returns None on any HTTP/parse failure or when no participants are scored,
        so the caller skips VHFT this cycle rather than crashing the scoring loop.

        TODO: the endpoint returns uid but not hotkey, so a uid that deregistered
        and re-registered between the scorer's window and the validator's metagraph
        could be misattributed. Add hotkey to /v1/scores and validate it against the
        metagraph for robustness.
        """
        try:
            resp = requests.get(self._url, timeout=_DEFAULT_TIMEOUT_S)
            resp.raise_for_status()
            rows = resp.json().get("scores", [])
        except Exception as e:
            bt.logging.warning(
                f"VHFT fetch_scores failed (skipping VHFT this cycle): {e}"
            )
            return None

        scores = {
            int(r["uid"]): float(r["mean_crps"])
            for r in rows
            if float(r.get("weight", 0.0)) > 0.0
        }
        if not scores:
            bt.logging.info("VHFT: no scored participants this cycle")
            return None
        return scores
