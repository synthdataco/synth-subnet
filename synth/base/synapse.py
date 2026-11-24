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

"""The request/response model of bittensor 10's axon/dendrite HTTP protocol.

Ported from bittensor 10.5.0 `bittensor/core/synapse.py`. Headers, body and
body hash must stay byte-compatible with bittensor 10 axons and dendrites.
"""

import base64
import hashlib
import json
import logging
import sys
from typing import Any, ClassVar, Optional, Union, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

# bittensor 10.4.1's version_as_int: the dendrite/axon version on the wire and
# the axon version served on chain. v10 axons apply the nonce window only from
# version 7002000.
WIRE_VERSION = 10_004_001


def get_hash(content: str, encoding: str = "utf-8") -> str:
    sha3 = hashlib.sha3_256()
    sha3.update(content.encode(encoding))
    return sha3.hexdigest()


def get_size(obj: Any, seen: Optional[set] = None) -> int:
    size = sys.getsizeof(obj)
    if seen is None:
        seen = set()
    obj_id = id(obj)
    if obj_id in seen:
        return 0
    seen.add(obj_id)
    if isinstance(obj, dict):
        size += sum([get_size(v, seen) for v in obj.values()])
        size += sum([get_size(k, seen) for k in obj.keys()])
    elif hasattr(obj, "__dict__"):
        size += get_size(obj.__dict__, seen)
    elif hasattr(obj, "__iter__") and not isinstance(
        obj, (str, bytes, bytearray)
    ):
        size += sum([get_size(i, seen) for i in obj])
    return size


def cast_int(raw: Optional[str]) -> Optional[int]:
    return int(raw) if raw is not None else raw


def cast_float(raw: Optional[str]) -> Optional[float]:
    return float(raw) if raw is not None else raw


class TerminalInfo(BaseModel):
    """One side (dendrite or axon) of a request, sent as bt_header_* headers."""

    model_config = ConfigDict(validate_assignment=True)

    status_code: Optional[int] = Field(default=None, frozen=False)
    status_message: Optional[str] = Field(default=None, frozen=False)
    process_time: Optional[float] = Field(default=None, frozen=False)
    ip: Optional[str] = Field(default=None, frozen=False)
    port: Optional[int] = Field(default=None, frozen=False)
    version: Optional[int] = Field(default=None, frozen=False)
    nonce: Optional[int] = Field(default=None, frozen=False)
    uuid: Optional[str] = Field(default=None, frozen=False)
    hotkey: Optional[str] = Field(default=None, frozen=False)
    signature: Optional[str] = Field(default=None, frozen=False)

    _extract_process_time = field_validator("process_time", mode="before")(
        cast_float
    )
    _extract_port = field_validator("port", mode="before")(cast_int)
    _extract_version = field_validator("version", mode="before")(cast_int)
    _extract_nonce = field_validator("nonce", mode="before")(cast_int)
    _extract_status_code = field_validator("status_code", mode="before")(
        cast_int
    )


class Synapse(BaseModel):
    """Base class of a request type; the subclass name is the URL path."""

    model_config = ConfigDict(validate_assignment=True)

    def deserialize(self) -> "Synapse":
        return self

    @model_validator(mode="before")
    def set_name_type(cls, values: dict) -> dict:
        values["name"] = cls.__name__  # type: ignore
        return values

    name: Optional[str] = Field(frozen=False, default=None, repr=False)
    timeout: Optional[float] = Field(default=12.0, frozen=False, repr=False)
    total_size: Optional[int] = Field(default=0, frozen=False, repr=False)
    header_size: Optional[int] = Field(default=0, frozen=False, repr=False)
    dendrite: Optional[TerminalInfo] = Field(
        default=TerminalInfo(), frozen=False, repr=False
    )
    axon: Optional[TerminalInfo] = Field(
        default=TerminalInfo(), frozen=False, repr=False
    )
    computed_body_hash: Optional[str] = Field(
        default="", frozen=True, repr=False
    )

    # Fields whose values are covered by the request signature.
    required_hash_fields: ClassVar[tuple[str, ...]] = ()

    _extract_total_size = field_validator("total_size", mode="before")(
        cast_int
    )
    _extract_header_size = field_validator("header_size", mode="before")(
        cast_int
    )
    _extract_timeout = field_validator("timeout", mode="before")(cast_float)

    def __setattr__(self, name: str, value: Any):
        if name == "body_hash":
            raise AttributeError(
                "body_hash property is read-only and cannot be overridden."
            )
        super().__setattr__(name, value)

    def get_total_size(self) -> int:
        self.total_size = get_size(self)
        return self.total_size

    @property
    def is_success(self) -> bool:
        return self.dendrite is not None and self.dendrite.status_code == 200

    @property
    def is_failure(self) -> bool:
        return self.dendrite is not None and self.dendrite.status_code != 200

    @property
    def is_timeout(self) -> bool:
        return self.dendrite is not None and self.dendrite.status_code == 408

    @property
    def is_blacklist(self) -> bool:
        return self.dendrite is not None and self.dendrite.status_code == 403

    @property
    def failed_verification(self) -> bool:
        return self.dendrite is not None and self.dendrite.status_code == 401

    def get_required_fields(self):
        schema = self.__class__.model_json_schema()
        return schema.get("required", [])

    def to_headers(self) -> dict:
        headers = {"name": self.name, "timeout": str(self.timeout)}
        if self.axon:
            headers.update(
                {
                    f"bt_header_axon_{k}": str(v)
                    for k, v in self.axon.model_dump().items()
                    if v is not None
                }
            )
        if self.dendrite:
            headers.update(
                {
                    f"bt_header_dendrite_{k}": str(v)
                    for k, v in self.dendrite.model_dump().items()
                    if v is not None
                }
            )

        # Required fields are sent as an empty instance of their dumped type.
        instance_fields = self.model_dump()
        required = self.get_required_fields()
        for field, value in instance_fields.items():
            if field in headers or value is None:
                continue
            elif required and field in required:
                try:
                    serialized_value = json.dumps(value.__class__.__call__())
                    encoded_value = base64.b64encode(
                        serialized_value.encode()
                    ).decode("utf-8")
                    headers[f"bt_header_input_obj_{field}"] = encoded_value
                except TypeError as e:
                    raise ValueError(
                        f"Error serializing {field} with value {value}. Objects must be json serializable."
                    ) from e

        headers["header_size"] = str(sys.getsizeof(headers))
        headers["total_size"] = str(self.get_total_size())
        headers["computed_body_hash"] = self.body_hash
        return headers

    @property
    def body_hash(self) -> str:
        hashes = []
        required_hash_fields = self.__class__.required_hash_fields
        if required_hash_fields:
            instance_fields = self.model_dump()
            for field in required_hash_fields:
                hashes.append(get_hash(str(instance_fields[field])))
        return get_hash("".join(hashes))

    @classmethod
    def parse_headers_to_inputs(cls, headers: dict) -> dict:
        inputs_dict: dict[str, Union[dict, Optional[str]]] = {
            "axon": {},
            "dendrite": {},
        }
        for key, value in headers.items():
            if "bt_header_axon_" in key:
                new_key = key.split("bt_header_axon_")[1]
                cast(dict, inputs_dict["axon"])[new_key] = value
            elif "bt_header_dendrite_" in key:
                new_key = key.split("bt_header_dendrite_")[1]
                cast(dict, inputs_dict["dendrite"])[new_key] = value
            elif "bt_header_input_obj" in key:
                try:
                    new_key = key.split("bt_header_input_obj_")[1]
                    if new_key in inputs_dict:
                        continue
                    inputs_dict[new_key] = json.loads(
                        base64.b64decode(value.encode()).decode("utf-8")
                    )
                except Exception as e:
                    logger.error(
                        f"Error while parsing 'input_obj' header {key}: {e}"
                    )

        inputs_dict["timeout"] = headers.get("timeout", None)
        inputs_dict["name"] = headers.get("name", None)
        inputs_dict["header_size"] = headers.get("header_size", None)
        inputs_dict["total_size"] = headers.get("total_size", None)
        inputs_dict["computed_body_hash"] = headers.get(
            "computed_body_hash", None
        )
        return inputs_dict

    @classmethod
    def from_headers(cls, headers: dict) -> "Synapse":
        input_dict = cls.parse_headers_to_inputs(headers)
        return cls(**input_dict)
