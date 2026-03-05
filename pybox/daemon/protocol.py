"""Daemon IPC protocol: msgpack-encoded request/response over Unix socket.

Wire format:
    [4-byte big-endian length][msgpack payload]

Message format:
    Request:  {"id": int, "method": str, "params": dict}
    Response: {"id": int, "result": any | None, "error": str | None}

Streaming responses (for logs/exec) use multiple response messages
with result being a bytes chunk, terminated by result=None.
"""

from __future__ import annotations

import asyncio
import struct
from enum import Enum
from typing import Any

import msgpack
from pydantic import BaseModel


class Method(str, Enum):
    """All supported daemon RPC methods."""

    RUN = "run"
    BUILD = "build"
    PS = "ps"
    INSPECT = "inspect"
    STOP = "stop"
    RM = "rm"
    EXEC = "exec"
    LOGS = "logs"
    IMAGES = "images"
    INFO = "info"


class DaemonRequest(BaseModel):
    """A JSON-RPC-style request sent to pyboxd."""

    id: int
    method: Method
    params: dict[str, Any] = {}


class DaemonResponse(BaseModel):
    """A response from pyboxd to a CLI client."""

    id: int
    result: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ------------------------------------------------------------------
# Wire encoding / decoding
# ------------------------------------------------------------------

def encode(msg: DaemonRequest | DaemonResponse) -> bytes:
    """Encode a message to wire format: [4-byte len][msgpack].

    Args:
        msg: Request or Response to encode.

    Returns:
        Bytes ready to send over the socket.
    """
    payload = msgpack.packb(msg.model_dump(mode="json"), use_bin_type=True)
    length = struct.pack(">I", len(payload))
    return length + payload


def decode_request(data: bytes) -> DaemonRequest:
    """Decode a msgpack payload into a DaemonRequest.

    Args:
        data: Raw msgpack bytes (without length prefix).

    Returns:
        Validated DaemonRequest.
    """
    raw = msgpack.unpackb(data, raw=False)
    return DaemonRequest.model_validate(raw)


def decode_response(data: bytes) -> DaemonResponse:
    """Decode a msgpack payload into a DaemonResponse.

    Args:
        data: Raw msgpack bytes (without length prefix).

    Returns:
        Validated DaemonResponse.
    """
    raw = msgpack.unpackb(data, raw=False)
    return DaemonResponse.model_validate(raw)


async def read_message(reader: asyncio.StreamReader) -> bytes:
    """Read one length-prefixed message from an asyncio StreamReader.

    Args:
        reader: asyncio StreamReader connected to the daemon socket.

    Returns:
        Raw msgpack bytes of the message body.

    Raises:
        EOFError: If the connection is closed.
    """

    length_data = await reader.readexactly(4)
    length = struct.unpack(">I", length_data)[0]
    return await reader.readexactly(length)
