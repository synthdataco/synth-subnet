"""A subnet's neurons read with bittensor 11, exposed with the attributes
and dtypes of bittensor 10's lite metagraph (the values stored in
metagraph_history must not change)."""

from dataclasses import dataclass
from typing import Any, Optional

import bittensor as bt
import netaddr
import numpy as np


@dataclass
class AxonInfo:
    """A neuron's served endpoint, with bittensor 10's AxonInfo fields,
    equality and parameter dict."""

    version: int
    ip: str
    port: int
    ip_type: int
    hotkey: str
    coldkey: str
    protocol: int = 4
    placeholder1: int = 0
    placeholder2: int = 0

    @property
    def is_serving(self) -> bool:
        return self.ip != "0.0.0.0"

    def ip_str(self) -> str:
        return "/ipv%i/%s:%i" % (self.ip_type, self.ip, self.port)

    def __eq__(self, other: object) -> bool:
        # protocol and placeholders are not compared.
        if not isinstance(other, AxonInfo):
            return False
        return (
            self.version == other.version
            and self.ip == other.ip
            and self.port == other.port
            and self.ip_type == other.ip_type
            and self.coldkey == other.coldkey
            and self.hotkey == other.hotkey
        )

    def to_parameter_dict(self) -> dict[str, Any]:
        return self.__dict__

    @classmethod
    def from_parameter_dict(cls, parameter_dict: dict[str, Any]) -> "AxonInfo":
        return cls(**parameter_dict)


class Metagraph:
    """One subnet's neurons at one block (the head by default)."""

    def __init__(
        self,
        subtensor: bt.Subtensor,
        netuid: int,
        block: Optional[int] = None,
    ):
        self.netuid = netuid
        self.sync(subtensor=subtensor, block=block)

    def sync(
        self, subtensor: bt.Subtensor, block: Optional[int] = None
    ) -> None:
        graph = subtensor.subnets.metagraph(
            self.netuid, block=block, commitments=False
        )
        if graph is None:
            raise ValueError(f"netuid {self.netuid} does not exist")
        neurons = graph.neurons
        self.block = np.array(graph.block, dtype=np.int64)
        self.n = np.array(len(neurons), dtype=np.int64)
        self.uids = np.array([n.uid for n in neurons], dtype=np.int64)
        # Stake weight in alpha (alpha stake + TaoWeight x root TAO stake).
        self.S = np.array(
            [n.total_stake.amount for n in neurons], dtype=np.float32
        )
        self.I = np.array(  # noqa: E741
            [n.incentive for n in neurons], dtype=np.float32
        )
        # Alpha per epoch.
        self.E = np.array(
            [n.emission.amount for n in neurons], dtype=np.float32
        )
        self.validator_permit = np.array(
            [n.validator_permit for n in neurons], dtype=bool
        )
        self.last_update = np.array(
            [n.last_update for n in neurons], dtype=np.int64
        )
        # Version-less netaddr parse: ints up to 2**32 - 1 are IPv4.
        self.axons = [
            AxonInfo(
                version=int(axon["version"]),
                ip=str(netaddr.IPAddress(int(axon["ip"]))),
                port=int(axon["port"]),
                ip_type=int(axon["ip_type"]),
                hotkey=n.hotkey,
                coldkey=n.coldkey,
                protocol=int(axon["protocol"]),
                placeholder1=int(axon["placeholder1"]),
                placeholder2=int(axon["placeholder2"]),
            )
            for n, axon in zip(neurons, graph.raw["axons"])
        ]

    @property
    def hotkeys(self) -> list[str]:
        return [axon.hotkey for axon in self.axons]

    @property
    def coldkeys(self) -> list[str]:
        return [axon.coldkey for axon in self.axons]

    @property
    def addresses(self) -> list[str]:
        return [axon.ip_str() for axon in self.axons]
