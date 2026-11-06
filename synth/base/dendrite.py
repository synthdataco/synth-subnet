import asyncio
import logging
import uuid
from typing import List, Tuple, Type, Union

import bittensor as bt
import httpx
import uvloop
from pydantic import ValidationError

from synth.protocol import Simulation
from synth.utils.networking import get_external_ip

logger = logging.getLogger(__name__)

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

_ERROR_MAPPINGS: List[Tuple[Type[Exception], Tuple[Union[str, None], str]]] = [
    # timeouts (asyncio + httpx)
    (asyncio.TimeoutError, ("408", "Request timeout")),
    (httpx.ReadTimeout, ("408", "Read timeout")),
    (httpx.WriteTimeout, ("408", "Write timeout")),
    (httpx.ConnectTimeout, ("408", "Request timeout")),
    # httpx connection issues
    (httpx.ConnectError, ("503", "Service unavailable")),
    (httpx.PoolTimeout, ("503", "Connection pool timeout")),
    (httpx.ReadError, ("503", "Read error")),
    # httpx protocol errors
    (httpx.RemoteProtocolError, ("502", "Protocol error")),
    (httpx.ProtocolError, ("502", "Protocol error")),
    (httpx.UnsupportedProtocol, ("400", "Unsupported protocol")),
    # httpx decoding & status
    (httpx.DecodingError, ("400", "Response decoding error")),
    (httpx.HTTPStatusError, (None, "Client response error")),
    # catch‐alls
    (httpx.RequestError, ("500", "Request error")),
    (httpx.HTTPError, (None, "HTTP error")),
]

_DENDRITE_DEFAULT_ERROR: Tuple[str, str] = ("422", "Failed to parse response")


def process_error_message(
    synapse: Simulation,
    request_name: str,
    exception: Exception,
):
    log_exception(exception)

    status_code, status_message = None, str(type(exception))
    for exc_type, (code, default_msg) in _ERROR_MAPPINGS:
        if isinstance(exception, exc_type):
            status_code, status_message = code, default_msg
            break

    if status_code is None:
        if isinstance(exception, httpx.HTTPStatusError):
            status_code = str(exception.response.status_code)
        else:
            # last‐ditch fallback
            status_code = _DENDRITE_DEFAULT_ERROR[0]

    if isinstance(exception, httpx.HTTPStatusError):
        host = getattr(synapse.axon, "ip", "<unknown>")
        port = getattr(synapse.axon, "port", "<unknown>")
        message = f"{status_message} at {host}:{port}/{request_name}"
    elif isinstance(exception, (asyncio.TimeoutError, httpx.ReadTimeout)):
        timeout = getattr(synapse, "timeout", "<unknown>")
        message = f"{status_message} after {timeout} seconds"
    else:
        message = f"{status_message}: {exception}"

    synapse.dendrite.status_code = status_code
    synapse.dendrite.status_message = message

    return synapse


class SynthDendrite:
    """The validator's request identity: its hotkey, a uuid for the life of
    the process and its external ip, sent in every request's headers."""

    def __init__(self, wallet: bt.Wallet):
        self.keypair = wallet.hotkey
        self.uuid = str(uuid.uuid1())
        self.external_ip = get_external_ip()

    def __str__(self) -> str:
        return f"dendrite({self.keypair.ss58_address})"


def log_exception(exception: Exception):
    """
    Logs an exception with a unique identifier.

    This method generates a unique UUID for the error, extracts the error type,
    and logs the error message.

    Args:
        exception (Exception): The exception object to be logged.

    Returns:
        None
    """
    error_id = str(uuid.uuid4())
    error_type = exception.__class__.__name__
    if isinstance(
        exception,
        (
            asyncio.TimeoutError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.HTTPStatusError,
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.RemoteProtocolError,
            ValidationError,
        ),
    ):
        pass
    else:
        logger.exception(f"{error_type}#{error_id}: {exception}")
