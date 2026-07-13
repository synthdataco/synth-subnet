from dataclasses import dataclass


@dataclass
class PromptConfig:
    asset_list: list[str]
    label: str
    time_length: int
    time_increment: int
    cycle_interval_minutes: int
    timeout_extra_seconds: int
    cycle_offset_minutes: int | None = None
    num_simulations: int = 1000
    data_retention_days: int = 30
    # Density tapering: predictions on validator_requests whose start_time is
    # older than `thin_after_minutes` get soft-deleted down to one row per
    # (miner_id, asset, bucket of `thin_bucket_seconds`). Keeps short-term
    # density for the short scoring intervals while pruning the long tail.
    thin_after_minutes: int = 30
    thin_bucket_seconds: int = 3600


LOW_FREQUENCY = PromptConfig(
    asset_list=[
        "BTC",
        "ETH",
        "SOL",
        "XRP",
        "HYPE",
        "XAU",
        "SPYX",
        "NVDAX",
        "GOOGLX",
        "TSLAX",
        "AAPLX",
        "WTIOIL",
        "SPCX",
    ],
    label="low",
    time_length=86400,
    time_increment=300,
    cycle_interval_minutes=5,
    timeout_extra_seconds=60,
    data_retention_days=11,
    thin_after_minutes=30,
    thin_bucket_seconds=3600,
)

HIGH_FREQUENCY = PromptConfig(
    asset_list=["BTC", "ETH", "SOL", "XRP", "HYPE"],
    label="high",
    time_length=3600,
    time_increment=60,
    cycle_interval_minutes=2,
    timeout_extra_seconds=0,
    data_retention_days=6,
    thin_after_minutes=10,
    thin_bucket_seconds=600,
)


def label_from_time_length(time_length: int) -> str:
    """Return the prompt label ('low' or 'high') for a given time_length.

    Raises ValueError on unknown values — Bigtable routes writes to one of
    two tables based on this label, so a silent fallback could mis-shelve
    a future third cycle.
    """
    if time_length == HIGH_FREQUENCY.time_length:
        return HIGH_FREQUENCY.label
    if time_length == LOW_FREQUENCY.time_length:
        return LOW_FREQUENCY.label
    raise ValueError(
        f"label_from_time_length: unknown time_length {time_length} "
        f"(known: {LOW_FREQUENCY.time_length}, {HIGH_FREQUENCY.time_length})"
    )
