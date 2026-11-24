import logging

import bittensor as bt
from bittensor.settings import DEFAULT_ERA_PERIOD

from synth.base.synapse import WIRE_VERSION
from synth.utils.networking import ip_to_int, ip_version

logger = logging.getLogger(__name__)

AXON_PROTOCOL = 4


def serve_axon(
    subtensor: bt.Subtensor,
    wallet: bt.Wallet,
    netuid: int,
    ip: str,
    port: int,
):
    """Publish ip:port as this hotkey's axon on chain, unless the chain
    already holds the same endpoint. Failures are logged, not raised."""
    try:
        _serve_axon(subtensor, wallet, netuid, ip, port)
    except Exception as error:
        logger.error(f"Failed to serve axon on {ip}:{port}: {error}")


def _serve_axon(
    subtensor: bt.Subtensor,
    wallet: bt.Wallet,
    netuid: int,
    ip: str,
    port: int,
):
    hotkey = wallet.hotkey.ss58_address
    wanted = {
        "version": WIRE_VERSION,
        "ip": ip_to_int(ip),
        "port": port,
        "ip_type": ip_version(ip),
        "protocol": AXON_PROTOCOL,
        "placeholder1": 0,
        "placeholder2": 0,
    }
    current = subtensor.query(
        bt.storage.SubtensorModule.Axons, [netuid, hotkey]
    )
    if isinstance(current, dict) and all(
        int(current.get(key) or 0) == value for key, value in wanted.items()
    ):
        logger.info(f"Axon already served on: {hotkey} {ip}:{port}")
        return

    result = subtensor.execute(
        bt.ServeAxon(
            netuid=netuid,
            ip=ip,
            port=port,
            protocol=AXON_PROTOCOL,
            version=WIRE_VERSION,
        ),
        wallet,
        period=DEFAULT_ERA_PERIOD,
    )
    if result.success:
        logger.info(f"Served axon {hotkey} on {ip}:{port}")
    else:
        logger.error(f"Failed to serve axon on {ip}:{port}: {result.message}")
