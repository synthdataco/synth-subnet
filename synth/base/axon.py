# The MIT License (MIT)
# Copyright © 2021 Yuma Rao
# Copyright © 2023 Opentensor Foundation

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

"""The miner's HTTP server, speaking bittensor 10's axon protocol.

Ported from bittensor 10.5.0 `bittensor/core/axon.py`: routes, request
signature check, nonce rules, status codes and response envelope must stay
compatible with bittensor 10 dendrites.
"""

import inspect
import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Optional

import bittensor as bt
import uvicorn
from bittensor.sp_core import Keypair
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from synth.base.serving import serve_axon
from synth.base.synapse import WIRE_VERSION, Synapse, TerminalInfo
from synth.utils.networking import get_external_ip

logger = logging.getLogger(__name__)

V_7_2_0 = 7002000
ALLOWED_DELTA = 4_000_000_000
NANOSECONDS_IN_SECOND = 1_000_000_000


class AxonError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def ping(synapse: Synapse) -> Synapse:
    return synapse


async def resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class Axon:
    """Serves each attached Synapse subclass at /<ClassName>; the first path
    segment selects the subclass."""

    def __init__(
        self,
        wallet: bt.Wallet,
        port: int,
        external_ip: Optional[str] = None,
        external_port: Optional[int] = None,
    ):
        self.wallet = wallet
        self.uuid = str(uuid.uuid1())
        self.port = port
        self.external_ip = (
            external_ip if external_ip is not None else get_external_ip()
        )
        self.external_port = (
            external_port if external_port is not None else port
        )
        # Last verified nonce per "<dendrite hotkey>:<dendrite uuid>".
        self.nonces: dict[str, int] = {}
        self.forward_class_types: dict[str, type[Synapse]] = {}
        self.forward_fns: dict[str, Callable] = {}
        self.blacklist_fns: dict[str, Optional[Callable]] = {}
        self.priority_fns: dict[str, Optional[Callable]] = {}

        self.app = Starlette(
            routes=[
                Route("/{path:path}", self.handle, methods=["GET", "POST"])
            ]
        )
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app, host="0.0.0.0", port=port, log_level="critical"
            )
        )
        self.thread: Optional[threading.Thread] = None
        self.attach(forward_fn=ping)

    def __str__(self) -> str:
        return (
            f"Axon({self.external_ip}:{self.external_port}, "
            f"{self.wallet.hotkey.ss58_address}, "
            f"{list(self.forward_class_types)})"
        )

    def attach(
        self,
        forward_fn: Callable,
        blacklist_fn: Optional[Callable] = None,
        priority_fn: Optional[Callable] = None,
    ) -> "Axon":
        """Route requests for the Synapse subclass annotating forward_fn's
        first parameter to forward_fn."""
        first_param = next(
            iter(inspect.signature(forward_fn).parameters.values())
        )
        synapse_class = first_param.annotation
        if not (
            inspect.isclass(synapse_class)
            and issubclass(synapse_class, Synapse)
        ):
            raise TypeError(
                "The first argument of forward_fn must be annotated with a "
                "Synapse subclass"
            )
        request_name = synapse_class.__name__
        self.forward_class_types[request_name] = synapse_class
        self.forward_fns[request_name] = forward_fn
        self.blacklist_fns[request_name] = blacklist_fn
        self.priority_fns[request_name] = priority_fn
        return self

    def start(self) -> "Axon":
        self.server.should_exit = False
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> "Axon":
        self.server.should_exit = True
        return self

    def serve(self, netuid: int, subtensor: bt.Subtensor) -> "Axon":
        serve_axon(
            subtensor,
            self.wallet,
            netuid,
            self.external_ip,
            self.external_port,
        )
        return self

    async def handle(self, request: Request) -> Response:
        start_time = time.time()
        request_name = request.url.path.split("/")[1]
        synapse_class = self.forward_class_types.get(request_name)
        if synapse_class is None:
            return error_response(
                Synapse(),
                404,
                f"Synapse name '{request_name}' not found. "
                f"Available synapses {list(self.forward_class_types)}",
                start_time,
            )

        try:
            synapse = synapse_class.from_headers(request.headers)
        except (ValidationError, TypeError, ValueError) as e:
            return internal_error_response(Synapse(), e, start_time)
        synapse.name = request_name
        self.sign_header_synapse(synapse, request)

        try:
            await self.blacklist(synapse)
            self.verify(synapse)
            priority_fn = self.priority_fns[request_name]
            if priority_fn is not None:
                await resolve(priority_fn(synapse))

            body_synapse = parse_body(
                synapse_class,
                await request.body(),
                request.headers.get("computed_body_hash", ""),
            )
            forward_start = time.time()
            response_synapse = await resolve(
                self.forward_fns[request_name](body_synapse)
            )
            return success_response(response_synapse, forward_start)
        except AxonError as e:
            return error_response(
                synapse, e.status_code, e.message, start_time
            )
        except Exception as e:
            return internal_error_response(synapse, e, start_time)

    def sign_header_synapse(self, synapse: Synapse, request: Request):
        """Fill the axon side of a header-parsed synapse; these values reach
        the wire only in error responses."""
        assert synapse.axon is not None and synapse.dendrite is not None
        synapse.axon.__dict__.update(
            {
                "version": WIRE_VERSION,
                "uuid": self.uuid,
                "nonce": time.time_ns(),
                "status_code": 100,
            }
        )
        if request.client is not None:
            synapse.dendrite.__dict__.update(
                {"port": request.client.port, "ip": request.client.host}
            )
        message = (
            f"{synapse.axon.nonce}.{synapse.dendrite.hotkey}."
            f"{synapse.axon.hotkey}.{synapse.axon.uuid}"
        )
        synapse.axon.signature = f"0x{self.wallet.hotkey.sign(message).hex()}"

    async def blacklist(self, synapse: Synapse):
        blacklist_fn = self.blacklist_fns[str(synapse.name)]
        if blacklist_fn is None:
            return
        blacklisted, reason = await resolve(blacklist_fn(synapse))
        if blacklisted:
            raise AxonError(403, f"Forbidden. Key is blacklisted: {reason}.")

    def verify(self, synapse: Synapse):
        try:
            self.verify_signature(synapse)
        except Exception as e:
            raise AxonError(401, f"Not Verified with error: {e}")

    def verify_signature(self, synapse: Synapse):
        """bittensor 10's default_verify: nonce freshness, then the dendrite
        signature over "nonce.dendrite_hotkey.own_hotkey.uuid.body_hash"."""
        dendrite = synapse.dendrite
        if dendrite is None:
            raise Exception("Synapse Dendrite is None")
        keypair = Keypair(ss58_address=dendrite.hotkey)
        message = (
            f"{dendrite.nonce}.{dendrite.hotkey}."
            f"{self.wallet.hotkey.ss58_address}.{dendrite.uuid}."
            f"{synapse.computed_body_hash}"
        )
        endpoint_key = f"{dendrite.hotkey}:{dendrite.uuid}"
        last_nonce = self.nonces.get(endpoint_key)

        if dendrite.nonce is None:
            raise Exception("Missing Nonce")

        if dendrite.version is not None and dendrite.version >= V_7_2_0:
            current_time_ns = time.time_ns()
            timeout_ns = (synapse.timeout or 0) * NANOSECONDS_IN_SECOND
            allowed_window_ns = current_time_ns - ALLOWED_DELTA - timeout_ns
            if last_nonce is None and dendrite.nonce <= allowed_window_ns:
                diff_seconds = (
                    current_time_ns - dendrite.nonce
                ) / NANOSECONDS_IN_SECOND
                allowed_delta_seconds = (
                    ALLOWED_DELTA + timeout_ns
                ) / NANOSECONDS_IN_SECOND
                raise Exception(
                    f"Nonce is too old: acceptable delta is {allowed_delta_seconds:.2f} seconds but request was {diff_seconds:.2f} seconds old"
                )

        if last_nonce is not None and dendrite.nonce <= last_nonce:
            raise Exception("Nonce is too old, a newer one was last processed")

        if not dendrite.signature:
            raise Exception("Missing Signature")

        if not dendrite.signature.startswith("0x"):
            raise Exception("Invalid signature format. Expected hex string.")

        signature = bytes.fromhex(dendrite.signature[2:])
        if not keypair.verify(message.encode(), signature):
            raise Exception(
                f"Signature mismatch with {message} and {dendrite.signature}"
            )

        self.nonces[endpoint_key] = dendrite.nonce


def parse_body(
    synapse_class: type[Synapse], body: bytes, computed_body_hash: str
) -> Synapse:
    synapse = synapse_class(**json.loads(body))
    if synapse.body_hash != computed_body_hash:
        raise ValueError(
            f"Hash mismatch between header body hash {computed_body_hash} "
            f"and parsed body hash {synapse.body_hash}"
        )
    return synapse


def success_response(synapse: Synapse, start_time: float) -> Response:
    if synapse.axon is None:
        synapse.axon = TerminalInfo()
    if synapse.axon.status_code is None:
        synapse.axon.status_code = 200
    if synapse.axon.status_code == 200 and not synapse.axon.status_message:
        synapse.axon.status_message = "Success"
    synapse.axon.process_time = time.time() - start_time

    response = JSONResponse(
        status_code=synapse.axon.status_code,
        content=synapse.model_dump(mode="json"),
    )
    response.headers.update(synapse.to_headers())
    return response


def error_response(
    synapse: Synapse, status_code: int, message: str, start_time: float
) -> Response:
    if synapse.axon is None:
        synapse.axon = TerminalInfo()
    synapse.axon.status_code = status_code
    synapse.axon.status_message = message
    synapse.axon.process_time = time.time() - start_time
    return JSONResponse(
        status_code=status_code,
        headers=synapse.to_headers(),
        content={"message": message},
    )


def internal_error_response(
    synapse: Synapse, exception: Exception, start_time: float
) -> Response:
    error_id = str(uuid.uuid4())
    logger.error(f"{exception.__class__.__name__}#{error_id}: {exception}")
    return error_response(
        synapse, 500, f"Internal Server Error #{error_id}", start_time
    )
