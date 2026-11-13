#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Pick the autoquant champion to serve, from its published verdicts.

Reads github.com/synthdataco/autoquant at one commit: a partial clone in
--cache-dir, refreshed on every run, or an existing clone with --autoquant.
Among the legs whose cutoff-30d verdict is eligible, it takes the campaign
whose verdict window starts last, then that campaign's best Score; Scores are
never compared across campaigns. Prints the pick as JSON on stdout, with the
Terraform values that deploy it and staleness warnings; logs go to stderr.
Standard library and git only; LFS files are never downloaded. Needs
Python 3.10 or later, which `uv run --script` provides:

    uv run --script pick_champion.py
    uv run --script pick_champion.py --list
    uv run --script pick_champion.py --campaign campaign-4 --leg gpt-6-astra
    uv run --script pick_champion.py --autoquant ~/autoquant --ref <sha>

A leg is eligible when its verdict-cutoff-30d/verdict.json was scored by the
current harness, has no blocking failed asset, covers every asset of
synth/validator/competition_config.py under --synth-subnet and names the
leg's CHAMPION sha, and the leg publishes a champion_source/ tree. Exits 1
when nothing is eligible, or when --leg names an ineligible leg without
--force.
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_URL = "https://github.com/synthdataco/autoquant"
RESULTS = "campaign_results"
TAG = "cutoff-30d"
SOURCE_DIR = "champion_source"
CONFIG = "synth/validator/competition_config.py"
# Must match RETIRED_ASSETS in autoquant's synth_bench/leaderboard.py.
RETIRED_ASSETS = {"SPYX": "2026-07-23"}
# First day of crypto-1h scoring with the volatility CRPS term.
VOL_CRPS_1H_DATE = "2026-09-22"
CUTOFF_STALE_DAYS = 45
WINDOW_STALE_DAYS = 30
# A cutoff-30d window is the 30 days after the data cutoff; its last 24h
# prompts settle about two days later.
WINDOW_DAYS = 30
SETTLE_DAYS = 2
GIT_ENV = {
    **os.environ,
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


@dataclass
class Leg:
    campaign: str
    name: str
    sha: str
    verdict: dict | None
    failures: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.campaign}/{self.name}"


def log(message: str) -> None:
    print(message, file=sys.stderr)


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    )
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def sync_cache(cache: Path) -> None:
    """Clone autoquant without blobs or a checkout, or fetch it if cached."""
    if (cache / ".git").is_dir():
        log(f"fetching {REPO_URL} into {cache}")
        git(cache, "fetch", "-q", "--prune", "origin")
        return
    log(f"cloning {REPO_URL} into {cache}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    git(
        cache.parent,
        "clone",
        "-q",
        "--filter=blob:none",
        "--no-checkout",
        REPO_URL,
        str(cache),
    )


def resolve(repo: Path, ref: str | None) -> str:
    """Full sha of --ref, or of origin/main, else main."""
    for candidate in [ref] if ref else ["origin/main", "main"]:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet"]
            + [f"{candidate}^{{commit}}"],
            capture_output=True,
            text=True,
            env=GIT_ENV,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    sys.exit(f"no commit {ref or 'origin/main or main'} in {repo}")


def list_tree(repo: Path, sha: str) -> dict[str, str]:
    """Path -> blob id of every file under campaign_results/."""
    tree = {}
    for entry in git(repo, "ls-tree", "-r", "-z", sha, "--", RESULTS).split(
        "\0"
    ):
        if entry:
            meta, path = entry.split("\t", 1)
            tree[path] = meta.split()[2]
    return tree


def is_metadata(path: str) -> bool:
    """campaign.yaml, CHAMPION or the verdict JSON: the files read here."""
    parts = path.split("/")
    if len(parts) == 3:
        return parts[2] == "campaign.yaml"
    if len(parts) == 4:
        return parts[3] == "CHAMPION"
    return path.endswith(f"/verdict-{TAG}/verdict.json")


def prefetch(repo: Path, blobs: list[str]) -> None:
    """Fetch these blobs from origin in one request."""
    git(
        repo,
        "-c",
        "fetch.negotiationAlgorithm=noop",
        "fetch",
        "-q",
        "--no-tags",
        "--no-write-fetch-head",
        "--filter=blob:none",
        "--stdin",
        "origin",
        stdin="\n".join(blobs) + "\n",
    )


def show(repo: Path, sha: str, path: str) -> str:
    return git(repo, "show", f"{sha}:{path}")


def parse_champion(text: str) -> tuple[str, str]:
    """(sha, agent_dir) of a CHAMPION file; `commit` is an alias of sha."""
    sha = re.search(r"^(?:sha|commit):\s*['\"]?([0-9a-fA-F]+)", text, re.M)
    agent_dir = re.search(r"^agent_dir:\s*['\"]?([^'\"\s]+)", text, re.M)
    return (
        sha.group(1).lower() if sha else "",
        agent_dir.group(1) if agent_dir else "",
    )


def current_assets(synth_subnet: Path) -> dict[str, list[str]]:
    """Slug -> assets of each competition in ALL_COMPETITIONS of the
    competition config, or {} when the file is absent."""
    path = synth_subnet / CONFIG
    if not path.exists():
        return {}
    configs: dict[str, list[str]] = {}
    names: list[str] = []
    for node in ast.parse(path.read_text()).body:
        if not isinstance(node, ast.Assign):
            continue
        target, value = node.targets[0], node.value
        if not isinstance(target, ast.Name):
            continue
        if target.id == "ALL_COMPETITIONS" and isinstance(value, ast.List):
            names = [e.id for e in value.elts if isinstance(e, ast.Name)]
        elif isinstance(value, ast.Call):
            for keyword in value.keywords:
                if keyword.arg == "asset_list":
                    configs[target.id] = ast.literal_eval(keyword.value)
    return {name.lower().replace("_", "-"): configs[name] for name in names}


def blocking_failures(verdict: dict) -> list[str]:
    """Failed assets other than a retirement the window starts after."""
    start = (verdict.get("window") or {}).get("start", "")
    out = []
    for slug, entry in (verdict.get("per_competition") or {}).items():
        for asset in entry.get("assets_failed") or []:
            retired = RETIRED_ASSETS.get(asset)
            if retired is None or start < retired:
                out.append(f"failed asset {slug}/{asset}")
    return out


def missing_assets(verdict: dict, assets: dict[str, list[str]]) -> list[str]:
    per_competition = verdict.get("per_competition") or {}
    missing = [
        f"{slug}/{asset}"
        for slug, names in assets.items()
        for asset in names
        if asset not in (per_competition.get(slug) or {}).get("per_asset", {})
    ]
    if not missing:
        return []
    return [f"verdict lacks current assets {', '.join(missing)}"]


def failures(
    leg: Leg,
    has_source: bool,
    assets: dict[str, list[str]],
) -> list[str]:
    out = [] if has_source else [f"no {SOURCE_DIR}/ with modeling.py"]
    verdict = leg.verdict
    if verdict is None:
        return out + [f"no verdict-{TAG}/verdict.json"]
    if not (verdict.get("engine_rev") and verdict.get("live_prompts") is True):
        out.append("verdict not scored by the current harness")
    verdict_sha = str(verdict.get("champion_sha") or "")
    if not leg.sha or not verdict_sha.startswith(leg.sha):
        out.append(
            f"CHAMPION sha {leg.sha} is not the verdict's {verdict_sha}"
        )
    return out + blocking_failures(verdict) + missing_assets(verdict, assets)


def load_legs(
    repo: Path,
    sha: str,
    tree: dict[str, str],
    assets: dict[str, list[str]],
) -> list[Leg]:
    """Every leg with a CHAMPION file, with its reasons to be ineligible."""
    legs = []
    for path in sorted(tree):
        parts = path.split("/")
        if len(parts) != 4 or parts[3] != "CHAMPION":
            continue
        leg_dir = "/".join(parts[:3])
        champion_sha, agent_dir = parse_champion(show(repo, sha, path))
        verdict_path = f"{leg_dir}/verdict-{TAG}/verdict.json"
        verdict = (
            json.loads(show(repo, sha, verdict_path))
            if verdict_path in tree
            else None
        )
        sources = [f"{leg_dir}/{SOURCE_DIR}/modeling.py"]
        if agent_dir:
            sources.append(f"{leg_dir}/{SOURCE_DIR}/{agent_dir}/modeling.py")
        leg = Leg(parts[1], parts[2], champion_sha, verdict)
        leg.failures = failures(leg, any(p in tree for p in sources), assets)
        legs.append(leg)
    return legs


def campaign_cutoffs(
    repo: Path, sha: str, tree: dict[str, str]
) -> dict[str, str | None]:
    """Campaign -> data_cutoff of its campaign.yaml, None when absent."""
    cutoffs: dict[str, str | None] = {}
    for path in sorted(tree):
        campaign = path.split("/")[1]
        if path == f"{RESULTS}/{campaign}/campaign.yaml":
            match = re.search(
                r"^data_cutoff:\s*['\"]?(\d{4}-\d{2}-\d{2})",
                show(repo, sha, path),
                re.M,
            )
            cutoffs[campaign] = match.group(1) if match else None
        else:
            cutoffs.setdefault(campaign, None)
    return cutoffs


def natural(name: str) -> list:
    """Sort key ordering campaign-10 after campaign-9."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def window(leg: Leg) -> dict:
    return (leg.verdict or {}).get("window") or {}


def rank_key(leg: Leg) -> tuple:
    """Higher Score, then higher mean reward/top, then lower mean of the
    per-competition mean ranks, then name."""
    verdict = leg.verdict or {}
    ranks = [
        (entry.get("rank_over_rounds") or {}).get("mean_rank")
        for entry in (verdict.get("per_competition") or {}).values()
    ]
    known = [rank for rank in ranks if rank is not None]
    mean_rank = sum(known) / len(known) if known else float("inf")
    return (
        -verdict["score"],
        -(verdict.get("mean_reward_vs_top") or 0.0),
        mean_rank,
        leg.name,
    )


def choose(
    legs: list[Leg], campaign: str | None, name: str | None, force: bool
) -> tuple[Leg, list[Leg], list[str]]:
    """(pick, ranked eligible legs of its campaign, warnings)."""
    eligible = [leg for leg in legs if not leg.failures]
    if name:
        return validate_choice(legs, eligible, f"{campaign}/{name}", force)
    if campaign:
        eligible = [leg for leg in eligible if leg.campaign == campaign]
    if not eligible:
        where = f" in {campaign}" if campaign else ""
        sys.exit(f"no eligible champion{where}: serve the stock miner")
    newest = max(
        eligible, key=lambda leg: (window(leg)["start"], natural(leg.campaign))
    ).campaign
    ranked = sorted(
        (leg for leg in eligible if leg.campaign == newest), key=rank_key
    )
    return ranked[0], ranked, []


def validate_choice(
    legs: list[Leg], eligible: list[Leg], leg_id: str, force: bool
) -> tuple[Leg, list[Leg], list[str]]:
    match = [leg for leg in legs if leg.id == leg_id]
    if not match:
        known = ", ".join(leg.id for leg in legs)
        sys.exit(f"no champion {leg_id}; champions: {known}")
    leg = match[0]
    if leg.failures and not force:
        sys.exit(
            f"{leg_id} is not eligible (--force outputs it anyway):\n  "
            + "\n  ".join(leg.failures)
        )
    ranked = sorted(
        (other for other in eligible if other.campaign == leg.campaign),
        key=rank_key,
    )
    warnings = [f"{leg_id} is not eligible: {why}" for why in leg.failures]
    if leg in ranked and ranked[0] is not leg:
        warnings.append(
            f"{leg_id} ranks {ranked.index(leg) + 1} of {len(ranked)} "
            f"eligible legs in {leg.campaign}; the best is {ranked[0].id}"
        )
    return leg, ranked, warnings


def staleness_warnings(leg: Leg, cutoff: str | None, today: date) -> list[str]:
    """Old data cutoff, old verdict window, window before the vol term."""
    out = []
    cutoff_days = (today - date.fromisoformat(cutoff)).days if cutoff else 0
    if cutoff_days > CUTOFF_STALE_DAYS:
        out.append(
            f"{leg.id} was fitted on data up to {cutoff}, "
            f"{cutoff_days} days ago"
        )
    end = window(leg).get("end")
    end_days = (today - date.fromisoformat(end)).days if end else 0
    if end_days > WINDOW_STALE_DAYS:
        out.append(
            f"{leg.id}'s verdict window ended {end}, {end_days} days ago"
        )
    start = window(leg).get("start")
    if start and start < VOL_CRPS_1H_DATE:
        out.append(
            f"{leg.id}'s verdict window starts {start}, before crypto-1h "
            f"scoring added the volatility term on {VOL_CRPS_1H_DATE}: part "
            "of its crypto-1h score predates it"
        )
    return out


def pending_warning(campaign: str, cutoff: str | None, today: date) -> str:
    """A campaign with champions and no verdict of the tag yet."""
    if not cutoff:
        return f"{campaign} has champions but no {TAG} verdict yet"
    ready = date.fromisoformat(cutoff) + timedelta(
        days=WINDOW_DAYS + SETTLE_DAYS
    )
    if ready <= today:
        return (
            f"{campaign} (data cutoff {cutoff}) has champions but no {TAG} "
            f"verdict; its window could be scored from {ready}"
        )
    return (
        f"{campaign} (data cutoff {cutoff}) has champions but no {TAG} "
        f"verdict yet; it can be scored from about {ready}, and its best "
        "leg may then replace this pick"
    )


def newer_campaign_warnings(
    leg: Leg, legs: list[Leg], cutoffs: dict[str, str | None], today: date
) -> list[str]:
    """Campaigns newer than the pick's that have champions it skips."""
    reference = cutoffs.get(leg.campaign) or window(leg).get("start")
    out = []
    for campaign, cutoff in cutoffs.items():
        own = [other for other in legs if other.campaign == campaign]
        if campaign == leg.campaign or not own:
            continue
        if cutoff and reference:
            newer = cutoff > reference
        else:
            newer = natural(campaign) > natural(leg.campaign)
        if not newer:
            continue
        if any(not other.failures for other in own):
            out.append(f"{campaign} is newer and has eligible legs")
        elif any(other.verdict is not None for other in own):
            out.append(f"{campaign} is newer but none of its legs is eligible")
        else:
            out.append(pending_warning(campaign, cutoff, today))
    return out


def competition_summary(entry: dict) -> dict:
    rounds = entry.get("rank_over_rounds") or {}
    reward_vs_top = (entry.get("rewards") or {}).get("reward_vs_top")
    return {
        "reward_vs_top": (
            None if reward_vs_top is None else round(reward_vs_top, 3)
        ),
        "mean_rank": rounds.get("mean_rank"),
        "best_rank": rounds.get("best_rank"),
        "field_size": rounds.get("field_size", entry.get("field_size")),
    }


def summary(
    leg: Leg,
    autoquant_sha: str,
    cutoff: str | None,
    ranked: list[Leg],
    warnings: list[str],
) -> dict:
    verdict = leg.verdict or {}
    return {
        "autoquant_sha": autoquant_sha,
        "campaign": leg.campaign,
        "leg": leg.name,
        "score": verdict.get("score"),
        "per_competition": {
            slug: competition_summary(entry)
            for slug, entry in (verdict.get("per_competition") or {}).items()
        },
        "window": {key: window(leg).get(key) for key in ("start", "end")},
        "data_cutoff": cutoff or window(leg).get("start"),
        "champion_sha": verdict.get("champion_sha") or leg.sha,
        "eligible_legs": [
            {"champion": other.id, "score": (other.verdict or {})["score"]}
            for other in ranked
        ],
        "warnings": warnings,
        "terraform": {"champion": leg.id, "autoquant_ref": autoquant_sha},
    }


def render_list(ranked: list[Leg]) -> str:
    """Padded table of the ranked legs: reward/top and mean rank per
    competition."""
    slugs = list((ranked[0].verdict or {})["per_competition"])
    rows = [["#", "Champion", "Score", *slugs]]
    for position, leg in enumerate(ranked, 1):
        verdict = leg.verdict or {}
        cells = []
        for slug in slugs:
            entry = competition_summary(verdict["per_competition"][slug])
            cells.append(
                f"{entry['reward_vs_top']:.3f} / {entry['mean_rank']:.1f}"
            )
        rows.append([str(position), leg.id, f"{verdict['score']:.1f}", *cells])
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = [
        "| " + " | ".join(c.ljust(w) for c, w in zip(row, widths)) + " |"
        for row in rows
    ]
    lines.insert(1, "| " + " | ".join("-" * w for w in widths) + " |")
    lines.append("Competition cells: reward/top / mean rank over the rounds.")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--autoquant", type=Path, help="existing autoquant clone, not fetched"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "synth-miner" / "autoquant",
        help="partial clone fetched when --autoquant is not set",
    )
    parser.add_argument(
        "--ref", help="autoquant commit (default: origin/main, else main)"
    )
    parser.add_argument(
        "--synth-subnet",
        type=Path,
        default=Path.cwd(),
        help=f"synth-subnet checkout holding {CONFIG} (default: cwd)",
    )
    parser.add_argument("--campaign", help="pick within this campaign")
    parser.add_argument("--leg", help="validate this leg (needs --campaign)")
    parser.add_argument(
        "--force", action="store_true", help="output an ineligible --leg"
    )
    parser.add_argument(
        "--list", action="store_true", help="print the ranked legs instead"
    )
    args = parser.parse_args()
    if args.leg and not args.campaign:
        parser.error("--leg needs --campaign")
    return args


def main() -> None:
    args = parse_args()
    repo = args.autoquant or args.cache_dir
    if args.autoquant is None:
        sync_cache(args.cache_dir)
    autoquant_sha = resolve(repo, args.ref)
    tree = list_tree(repo, autoquant_sha)
    if args.autoquant is None:
        prefetch(repo, [blob for p, blob in tree.items() if is_metadata(p)])
    log(f"autoquant {autoquant_sha} from {repo}")
    assets = current_assets(args.synth_subnet)
    warnings = (
        []
        if assets
        else [f"no {CONFIG} in {args.synth_subnet}: assets not checked"]
    )
    legs = load_legs(repo, autoquant_sha, tree, assets)
    for leg in legs:
        if leg.failures:
            log(f"skip {leg.id}: {'; '.join(leg.failures)}")
    cutoffs = campaign_cutoffs(repo, autoquant_sha, tree)
    leg, ranked, choice_warnings = choose(
        legs, args.campaign, args.leg, args.force
    )
    warnings += choice_warnings
    today = datetime.now(timezone.utc).date()
    warnings += staleness_warnings(leg, cutoffs.get(leg.campaign), today)
    warnings += newer_campaign_warnings(leg, legs, cutoffs, today)
    for warning in warnings:
        log(f"warning: {warning}")
    log(f"pick {leg.id}")
    if args.list:
        print(render_list(ranked) if ranked else "no eligible legs")
        return
    output = summary(
        leg, autoquant_sha, cutoffs.get(leg.campaign), ranked, warnings
    )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
