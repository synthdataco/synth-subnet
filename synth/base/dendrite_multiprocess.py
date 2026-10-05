import sys
import threading
import logging.handlers
import time
from typing import Union
import asyncio
import concurrent.futures
from concurrent.futures.process import BrokenProcessPool


import bittensor as bt
from bittensor.core.settings import version_as_int
import httpx
import uvloop


from synth.base.dendrite import process_error_message
from synth.protocol import Simulation
from synth.simulation_input import SimulationInput


class SubstringFilter(logging.Filter):
    def __init__(self, forbidden):
        super().__init__()
        self.forbidden = forbidden

    def filter(self, record):
        # keep only those records whose formatted message does *not* contain the substring
        return self.forbidden not in record.getMessage()


def setup_log_filter(forbidden_substring):
    logging.getLogger("bittensor").addFilter(
        SubstringFilter(forbidden_substring)
    )


def silent_thread_hook(args):
    if isinstance(args.exc_value, EOFError):
        # Ignore silently
        return
    # Fall back to default
    sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)


def safe_monitor(self):
    try:
        while True:
            try:
                record = self.dequeue(True)
            except EOFError:
                break
            except Exception:
                continue
            self.handle(record)
    except Exception:
        pass


if hasattr(logging.handlers, "QueueListener"):
    logging.handlers.QueueListener._monitor = safe_monitor


REQUEST_NAME = "Simulation"


def get_endpoint_url(
    external_ip: str, target_axon: bt.Axon, request_name: str = REQUEST_NAME
):
    endpoint = (
        f"0.0.0.0:{str(target_axon.port)}"
        if target_axon.ip == str(external_ip)
        else f"{target_axon.ip}:{str(target_axon.port)}"
    )
    return f"http://{endpoint}/{request_name}"


def preprocess_synapse_for_request(
    ss58_address: str,
    nonce: int,
    uuid: str,
    external_ip: str,
    target_axon_info: bt.AxonInfo,
    synapse: Simulation,
    timeout: float,
) -> Simulation:
    synapse.timeout = timeout
    synapse.dendrite = bt.TerminalInfo(
        ip=external_ip,
        version=version_as_int,
        nonce=nonce,
        uuid=uuid,
        hotkey=ss58_address,
    )

    # Build the Axon headers using the target axon's details
    synapse.axon = bt.TerminalInfo(
        ip=target_axon_info.ip,
        port=target_axon_info.port,
        hotkey=target_axon_info.hotkey,
    )

    return synapse


def process_server_response(
    status: int,
    headers: httpx.Headers,
    json_response: dict,
    local_synapse: Simulation,
):
    # Check if the server responded with a successful status code
    if status == 200:
        # If the response is successful, overwrite local synapse state with
        # server's state only if the protocol allows mutation. To prevent overwrites,
        # the protocol must set Frozen = True
        server_synapse = local_synapse.__class__(**json_response)
        key = "simulation_output"
        setattr(local_synapse, key, getattr(server_synapse, key))
    else:
        # If the server responded with an error, update the local synapse state
        if local_synapse.axon is None:
            local_synapse.axon = bt.TerminalInfo()
        local_synapse.axon.status_code = status
        local_synapse.axon.status_message = json_response.get("message")

    # Extract server headers and overwrite None values in local synapse headers
    server_headers = bt.Synapse.from_headers(headers)

    # Merge dendrite headers
    local_synapse.dendrite.__dict__.update(
        {
            **local_synapse.dendrite.model_dump(exclude_none=True),
            **server_headers.dendrite.model_dump(exclude_none=True),
        }
    )

    # Merge axon headers
    local_synapse.axon.__dict__.update(
        {
            **local_synapse.axon.model_dump(exclude_none=True),
            **server_headers.axon.model_dump(exclude_none=True),
        }
    )

    # Update the status code and status message of the dendrite to match the axon
    local_synapse.dendrite.status_code = local_synapse.axon.status_code
    local_synapse.dendrite.status_message = local_synapse.axon.status_message


async def call(
    ss58_address: str,
    nonce: int,
    signature: str,
    uuid: str,
    external_ip: str,
    client: httpx.AsyncClient,
    target_axon: Union[bt.AxonInfo, bt.Axon],
    synapse_headers: dict,
    synapse_body: dict,
    timeout: float,
):
    start_time = time.time()
    target_axon = (
        target_axon.info() if isinstance(target_axon, bt.Axon) else target_axon
    )

    url = get_endpoint_url(external_ip, target_axon)

    synapse = Simulation(simulation_input=SimulationInput()).from_headers(
        synapse_headers
    )

    # Preprocess synapse for making a request
    synapse = preprocess_synapse_for_request(
        ss58_address,
        nonce,
        uuid,
        external_ip,
        target_axon,
        synapse,
        timeout,
    )
    synapse.dendrite.signature = signature

    try:
        bt.logging.trace(
            f"dendrite | --> | {synapse.get_total_size()} B | {synapse.name} | {synapse.axon.hotkey} | {synapse.axon.ip}:{str(synapse.axon.port)} | 0 | Success"
        )
        # Enforce a total per-miner wall-clock timeout. httpx's `timeout` on
        # the AsyncClient is per-operation (connect/read/write/pool) and its
        # `read` timer resets on every received chunk, so a miner that
        # delivers its body incrementally can keep a request alive well past
        # `timeout`. Wrapping with asyncio.wait_for bounds the whole call.
        response = await asyncio.wait_for(
            client.post(
                url=url,
                headers=synapse.to_headers(),
                json=synapse_body,
            ),
            timeout=timeout,
        )
        response.raise_for_status()
        json_response = response.json()
        process_server_response(
            response.status_code, response.headers, json_response, synapse
        )

        synapse.dendrite.process_time = str(time.time() - start_time)

    except Exception as e:
        synapse = process_error_message(synapse, REQUEST_NAME, e)

    finally:
        bt.logging.trace(
            f"dendrite | <-- | {synapse.get_total_size()} B | {synapse.name} | {synapse.axon.hotkey} | {synapse.axon.ip}:{str(synapse.axon.port)} | {synapse.dendrite.status_code} | {synapse.dendrite.status_message}"
        )

        return [synapse.simulation_output, synapse.dendrite.process_time]


async def worker(
    ss58_address: str,
    nonce: int,
    uuid: str,
    external_ip: str,
    synapse_headers: dict,
    synapse_body: dict,
    axon_sig_pairs: list,
    timeout: float,
):
    async with httpx.AsyncClient(
        http2=True,
        limits=httpx.Limits(
            max_connections=None, max_keepalive_connections=25
        ),
        timeout=timeout,
    ) as client:
        return await asyncio.gather(
            *(
                call(
                    ss58_address=ss58_address,
                    nonce=nonce,
                    signature=signature,
                    uuid=uuid,
                    external_ip=external_ip,
                    client=client,
                    target_axon=bt.AxonInfo.from_parameter_dict(
                        axon_dict,
                    ),
                    synapse_headers=synapse_headers,
                    synapse_body=synapse_body,
                    timeout=timeout,
                )
                for axon_dict, signature in axon_sig_pairs
            )
        )


def run_chunk(
    ss58_address: str,
    nonce: int,
    uuid: str,
    external_ip: str,
    synapse_headers: dict,
    synapse_body: dict,
    axon_sig_pairs: list,
    timeout: float,
):
    try:
        return asyncio.run(
            worker(
                ss58_address,
                nonce,
                uuid,
                external_ip,
                synapse_headers,
                synapse_body,
                axon_sig_pairs,
                timeout,
            )
        )
    except EOFError:
        pass


def sign(synapse: Simulation, keypair: bt.Keypair):
    # Sign the request using the dendrite, axon info, and the synapse body hash
    message = f"{synapse.dendrite.nonce}.{synapse.dendrite.hotkey}.{synapse.axon.hotkey}.{synapse.dendrite.uuid}.{synapse.body_hash}"
    signature = f"0x{keypair.sign(message).hex()}"
    return signature


def chunkify(lst, n):
    k, m = divmod(len(lst), n)
    for i in range(n):
        yield lst[i * k + min(i, m) : (i + 1) * k + min(i + 1, m)]


def sign_axons(
    keypair: bt.Keypair,
    nonce: int,
    uuid: str,
    external_ip: str,
    axons: list[bt.AxonInfo],
    synapse: Simulation,
    timeout: float,
):
    synapse = synapse.model_copy()
    for axon in axons:
        synapse = preprocess_synapse_for_request(
            keypair.ss58_address,
            nonce,
            uuid,
            external_ip,
            axon,
            synapse,
            timeout,
        )
        yield sign(synapse, keypair)


# Reused across cycles; a broken pool is dropped so the next cycle forks a new one.
_PROCESS_EXECUTOR = None


def get_process_executor(
    nprocs: int,
) -> concurrent.futures.ProcessPoolExecutor:
    global _PROCESS_EXECUTOR
    if _PROCESS_EXECUTOR is None:
        _PROCESS_EXECUTOR = concurrent.futures.ProcessPoolExecutor(nprocs)
    return _PROCESS_EXECUTOR


def sync_forward_multiprocess(
    keypair: bt.Keypair,
    uuid: str,
    external_ip: str,
    axons: list[bt.AxonInfo],
    synapse: Simulation,
    timeout: float,
    nprocs: int = 2,
) -> list[list]:
    """Query every axon and return `[simulation_output, process_time]`
    pairs in the same order as `axons`."""
    global _PROCESS_EXECUTOR
    bt.logging.debug(
        f"Starting multiprocess forward with {nprocs} processes.", "dendrite"
    )
    ss58_address = keypair.ss58_address
    synapse = synapse.model_copy()
    nonce = time.time_ns()
    axon_dicts = [ax.to_parameter_dict() for ax in axons]
    signatures = list(
        sign_axons(keypair, nonce, uuid, external_ip, axons, synapse, timeout)
    )
    axon_sig_pairs = list(zip(axon_dicts, signatures))
    chunks = list(chunkify(axon_sig_pairs, nprocs))
    synapse_headers = synapse.to_headers()
    synapse_body = synapse.model_dump()

    executor = get_process_executor(nprocs)
    try:
        futures = {
            executor.submit(
                run_chunk,
                ss58_address,
                nonce,
                uuid,
                external_ip,
                synapse_headers,
                synapse_body,
                chunk,
                timeout,
            ): chunk_index
            for chunk_index, chunk in enumerate(chunks)
        }
        chunk_results: list = [None] * len(chunks)
        for future in concurrent.futures.as_completed(futures):
            chunk_results[futures[future]] = future.result()
    except BrokenProcessPool:
        _PROCESS_EXECUTOR = None
        raise

    return [result for chunk in chunk_results for result in chunk]


# Set the event loop policy to use uvloop for better performance
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

# Setup logging filter to ignore unwanted logs
setup_log_filter("Unexpected header key encountered")

# Silent EOFError
threading.excepthook = silent_thread_hook
