from dataclasses import dataclass

SMOOTHED_SCORE_COEFFICIENT = 1 / 3


@dataclass
class CompetitionConfig:
    asset_list: list[str]
    label: str
    time_length: int
    time_increment: int
    scoring_intervals: dict[str, int]  # Define scoring intervals in seconds.
    window_days: int
    softmax_beta: float


COM_EQU_24H = CompetitionConfig(
    asset_list=[
        "XAU",
        "NVDAX",
        "GOOGLX",
        "TSLAX",
        "AAPLX",
        "WTIOIL",
        "SPCX",
        "SP500",
    ],
    label="Commodities/Equities 24h",
    time_length=86400,
    time_increment=300,
    scoring_intervals={
        "5min": 300,  # 5 minutes
        "30min": 1800,  # 30 minutes
        "3hour": 10800,  # 3 hours
        "24hour_abs": 86400,  # 24 hours
    },
    window_days=10,
    softmax_beta=-0.15,
)

CRYPTO_24H = CompetitionConfig(
    asset_list=[
        "BTC",
        "ETH",
        "SOL",
        "XRP",
        "HYPE",
    ],
    label="Crypto 24h",
    time_length=86400,
    time_increment=300,
    scoring_intervals={
        "5min": 300,  # 5 minutes
        "30min": 1800,  # 30 minutes
        "3hour": 10800,  # 3 hours
        "24hour_abs": 86400,  # 24 hours
    },
    window_days=10,
    softmax_beta=-0.15,
)

CRYPTO_1H = CompetitionConfig(
    asset_list=[
        "BTC",
        "ETH",
        "SOL",
        "XRP",
        "HYPE",
    ],
    label="Crypto 1h",
    time_length=3600,
    time_increment=60,
    scoring_intervals={
        "1min": 60,
        "2min": 120,
        "5min": 300,
        "15min": 900,
        "30min": 1800,
        "60min_abs": 3600,
        "0_5min_gaps": 300,
        "0_10min_gaps": 600,
        "0_15min_gaps": 900,
        "0_20min_gaps": 1200,
        "0_25min_gaps": 1500,
        "0_30min_gaps": 1800,
        "0_35min_gaps": 2100,
        "0_40min_gaps": 2400,
        "0_45min_gaps": 2700,
        "0_50min_gaps": 3000,
        "0_55min_gaps": 3300,
        "0_60min_gaps": 3600,
    },
    window_days=5,
    softmax_beta=-0.3,
)


# Canonical list of competitions. Both the scoring cycle (neurons/validator.py)
# and the moving-average update (forward.py) iterate this, so a competition
# added/renamed/reordered here propagates to both — don't re-list elsewhere.
ALL_COMPETITIONS = [
    COM_EQU_24H,
    CRYPTO_24H,
    CRYPTO_1H,
]

# VHFT (Synth Ultra) — the 10-second BTC-microprice competition. Scored OFF-subnet
# and blended in via the external-ingestion path (vhft_score_provider +
# compute_vhft_smoothed_score), NOT through the inline-CRPS path — so it is
# deliberately kept OUT of ALL_COMPETITIONS. It reuses the same
# SMOOTHED_SCORE_COEFFICIENT and softmax as the other three, so after set_weights
# L1-normalizes, the four competitions split emissions equally (the coefficient
# value cancels; a *shared* coefficient is what makes the split equal).
VHFT_COMPETITION = CompetitionConfig(
    asset_list=["BTC"],
    label="VHFT 10s",
    time_length=10,
    time_increment=10,
    scoring_intervals={"10s": 10},
    # window_days is nominal — the external scorer already windows the scores, so
    # no per-window aggregation happens here.
    window_days=1,
    # softmax_beta = -2.0 (was -0.25) — steeper than every other competition by
    # design, not scale-matched to them. The softmax acts on ABSOLUTE mean_crps and
    # the field is tightly bunched, so beta converts a small CRPS gap into a large
    # weight gap: measured on the 2026-08-25 field (spread 6.537-7.207, ~10% of the
    # ~6.9 base), -0.25 gave a 1.18x best/worst ratio and a 31.6% top-3 share,
    # -2.0 gives 3.82x and 43.0%. The flip side is reward variance — rank shuffle
    # inside that narrow band now moves weight substantially.
    # MUST stay negative (lower CRPS = higher reward), same convention as the
    # other competitions.
    softmax_beta=-2.0,
)

# Plausible size of the VHFT participant field, enforced in
# compute_vhft_smoothed_score — outside this range the blend is skipped for the
# cycle rather than trusted.
#
# The VHFT block is a fixed SMOOTHED_SCORE_COEFFICIENT however many uids share
# it, so each participant's cut scales as 1/N: at 9 participants the top uid
# takes ~2.8% of all emissions, at 3 it takes ~8.3%, and at 1 it takes the whole
# 25%. A round that scores only a handful of miners — a degraded scorer, or a
# field that has mostly dropped out — would otherwise quietly hand one miner an
# outsized share, so require a floor. The ceiling is the other end of the same
# argument: a snapshot naming dozens of scored participants is a malfunctioning
# scorer rather than a competition that suddenly got popular.
VHFT_MIN_PARTICIPANTS = 3
VHFT_MAX_PARTICIPANTS = 64
