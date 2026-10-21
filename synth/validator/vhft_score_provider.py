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

from synth.validator.competition_config import VHFT_ASSETS

_DEFAULT_TIMEOUT_S = 10


def _parse_assets(spec: str) -> list[str]:
    """VHFT_REWARDED_ASSETS, e.g. "BTC,ETH"; empty means BTC alone.

    A repeated asset is refused: the block is split by the number of entries,
    but both would pay under the same competition label, so one half of that
    asset's share would silently vanish."""
    assets = [a.strip().upper() for a in spec.split(",") if a.strip()]
    unknown = [a for a in assets if a not in VHFT_ASSETS]
    if unknown:
        raise ValueError(
            f"VHFT_REWARDED_ASSETS: unknown {unknown}; known {VHFT_ASSETS}"
        )
    if len(set(assets)) != len(assets):
        raise ValueError(f"VHFT_REWARDED_ASSETS: repeated asset in {assets}")
    return assets or ["BTC"]


def _parse_score_row(row: typing.Any) -> typing.Optional[tuple[int, float]]:
    """Return (uid, mean_crps) for one scored participant.

    None means "not a participant this cycle" — the steady-state snapshot is 9
    scored uids and 247 at weight 0.0, so that is the common, expected case and
    not worth logging.

    Raises TypeError/ValueError/KeyError for a row that claims participation but
    cannot be used: a null or NEGATIVE mean_crps, or a malformed uid/weight.
    The caller counts those and warns, because unlike a weight-0.0 row they mean
    the scorer sent something unexpected.

    0.0 is a LEGITIMATE score, not a rejection: the scorer publishes field-relative
    scores (each prompt's best miner scores 0, matching the subnet's own
    compute_prompt_scores), so a miner that wins every prompt in the window averages
    exactly 0.0. Non-participants also read 0.0, which is why `weight > 0` — not the
    score — is the participation test, and why that check must stay first.
    """
    if float(row.get("weight") or 0.0) <= 0.0:
        return None
    mean_crps = row.get("mean_crps")
    if mean_crps is None:
        raise ValueError("null mean_crps on a positive-weight row")
    crps = float(mean_crps)
    if crps < 0.0:
        raise ValueError(f"negative mean_crps {crps!r}")
    return int(row["uid"]), crps


class VhftScoreProvider:
    def __init__(self, url: str, assets: typing.Optional[list[str]] = None):
        self._url = url
        # The VHFT assets paid as competitions, each an equal share of the block.
        self.assets = assets or ["BTC"]
        bt.logging.info(
            f"VHFT score provider enabled (url: {self._url}, "
            f"assets: {', '.join(self.assets)})"
        )

    @staticmethod
    def from_env() -> typing.Optional["VhftScoreProvider"]:
        """Build from VHFT_SCORES_URL and VHFT_REWARDED_ASSETS; returns None
        (disabled) when the URL is unset."""
        url = os.getenv("VHFT_SCORES_URL")
        if not url:
            return None
        assets = _parse_assets(os.getenv("VHFT_REWARDED_ASSETS", ""))
        return VhftScoreProvider(url=url, assets=assets)

    def _get_rows(self, asset: str) -> typing.Optional[list]:
        """The snapshot's rows for `asset`, or None when there are none to
        trust: the request failed, or the scorer answered for another asset."""
        params = None if asset == "BTC" else {"asset": asset}
        try:
            resp = requests.get(
                self._url, params=params, timeout=_DEFAULT_TIMEOUT_S
            )
            resp.raise_for_status()
            body = resp.json()
            rows = body.get("scores", [])
        except Exception as e:
            bt.logging.warning(
                f"VHFT {asset} fetch_scores failed (skipping it this cycle): "
                f"{e}"
            )
            return None
        if params is not None and body.get("asset") != asset:
            bt.logging.warning(
                f"VHFT: asked for {asset}, the scorer answered for "
                f"{body.get('asset')!r} (an older scorer without per-asset "
                f"scores); skipping {asset} this cycle"
            )
            return None
        return rows

    def fetch_scores(
        self, asset: str = "BTC"
    ) -> typing.Optional[dict[int, float]]:
        """Return {miner_uid: mean_crps} for one asset's VHFT participants only.

        BTC is the endpoint's default snapshot, fetched as it always was. Any
        other asset is `?asset=<ASSET>`, and the response must say so: an older
        scorer ignores the parameter and would answer with BTC's scores, which
        must never be paid out as another asset's.

        The /v1/scores endpoint returns all 256 uids, with mean_crps=0.0 (and
        weight=0.0) for non-participants. Those are EXCLUDED here: 0.0 would read
        as a *perfect* score to the lower-is-better softmax downstream, wrongly
        rewarding miners that never competed in VHFT. We keep only uids the scorer
        gave positive weight (i.e. actually-scored participants) and return their
        raw mean_crps (the blend applies its own softmax, so pass the raw score,
        not the pre-normalized weight).

        Rows are parsed defensively: mean_crps is null whenever the scorer could
        not attach one (older snapshots, or a uid/mean_crps length mismatch — see
        bigtable_scores_provider, where null is the deliberate sentinel because 0.0
        is a real perfect-CRPS value). A null on a positive-weight row is dropped
        rather than coerced, and so is any row with a malformed uid/weight.

        A NEGATIVE mean_crps is dropped (impossible by construction). 0.0 is NOT
        dropped, and that is a deliberate reversal: the scorer now publishes
        field-relative scores, where each prompt's best miner scores 0 (matching
        the subnet's own compute_prompt_scores), so the window's best miner
        legitimately averages 0.0. The previous rule — drop any non-positive score,
        justified because a genuine 0.0 needed a point-mass prediction landing
        exactly on the realized price — would now systematically exclude the
        WINNER from every cycle.

        The protection that rule provided is preserved by the degenerate-snapshot
        guard below. The incident it defends against is a post-restart placeholder
        snapshot: every uid at a nonzero weight and an identical score, which would
        spread the VHFT block's whole share across every registered miner, none of
        whom competed. That snapshot's signature is that ALL scores are identical,
        which is now what we test for — and unlike the old rule it catches a
        degenerate snapshot at any value, not only at 0.0. Two further layers back
        it up: the scorer refuses to publish while its book is empty, and
        compute_vhft_smoothed_score skips any field outside
        [VHFT_MIN_PARTICIPANTS, VHFT_MAX_PARTICIPANTS].

        Returns None on any HTTP/parse failure or when no participants are scored,
        so the caller skips VHFT this cycle rather than crashing the scoring loop.

        TODO: the endpoint returns uid but not hotkey, so a uid that deregistered
        and re-registered between the scorer's window and the validator's metagraph
        could be misattributed. Add hotkey to /v1/scores and validate it against the
        metagraph for robustness.
        """
        rows = self._get_rows(asset)
        if rows is None:
            return None

        scores: dict[int, float] = {}
        dropped = 0
        for r in rows:
            try:
                parsed = _parse_score_row(r)
            except (TypeError, ValueError, KeyError, AttributeError):
                dropped += 1
                continue
            if parsed is not None:
                scores[parsed[0]] = parsed[1]

        if dropped:
            bt.logging.warning(
                f"VHFT: dropped {dropped} unusable score row(s) "
                f"(null/negative mean_crps or malformed uid/weight)"
            )
        if not scores:
            bt.logging.info("VHFT: no scored participants this cycle")
            return None
        # Degenerate-snapshot guard. Real field-relative scores over a 24h window are
        # never all equal (that would need every miner to tie on every prompt), so an
        # identical-score field means a placeholder/degraded snapshot, not a result.
        # Replaces the old "drop non-positive crps" rule, which cannot survive scores
        # that legitimately start at 0. See the docstring.
        if len(scores) > 1 and len(set(scores.values())) == 1:
            bt.logging.warning(
                f"VHFT: all {len(scores)} scored uids share an identical score "
                f"({next(iter(scores.values()))!r}) — treating as a placeholder "
                f"snapshot and skipping VHFT this cycle"
            )
            return None
        return scores
