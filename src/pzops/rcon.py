"""A minimal Source RCON client (the protocol Project Zomboid's server speaks).

Packet layout, little-endian:
    int32 size (of everything after this field)
    int32 id
    int32 type
    body (null-terminated ASCII)
    null terminator
"""

from __future__ import annotations

import socket
import struct

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

MAX_PACKET = 4096


class RconError(RuntimeError):
    pass


class RconAuthError(RconError):
    pass


def encode_packet(packet_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", packet_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def decode_packet(payload: bytes) -> tuple[int, int, str]:
    packet_id, packet_type = struct.unpack("<ii", payload[:8])
    body = payload[8:].split(b"\x00", 1)[0]
    return packet_id, packet_type, body.decode("utf-8", errors="replace")


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self.host = host
        self.port = int(port)
        self.password = password
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._next_id = 0

    def __enter__(self) -> RconClient:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        packet_id = self._send(SERVERDATA_AUTH, self.password)
        response_id, response_type, _ = self._receive()
        # Some servers emit an empty RESPONSE_VALUE before the auth result.
        if response_type == SERVERDATA_RESPONSE_VALUE:
            response_id, _, _ = self._receive()
        if response_id == -1 or response_id != packet_id:
            self.close()
            raise RconAuthError("RCON authentication failed")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _send(self, packet_type: int, body: str) -> int:
        if self._sock is None:
            raise RconError("not connected")
        self._next_id += 1
        self._sock.sendall(encode_packet(self._next_id, packet_type, body))
        return self._next_id

    def _read_exactly(self, count: int) -> bytes:
        assert self._sock is not None
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise RconError("connection closed by server")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _receive(self) -> tuple[int, int, str]:
        size = struct.unpack("<i", self._read_exactly(4))[0]
        if not 0 < size <= MAX_PACKET:
            raise RconError(f"implausible RCON packet size: {size}")
        return decode_packet(self._read_exactly(size))

    def command(self, text: str) -> str:
        packet_id = self._send(SERVERDATA_EXECCOMMAND, text)
        response_id, _, body = self._receive()
        if response_id != packet_id:
            raise RconError("mismatched RCON response id")
        return body


def try_command(host: str, port: int, password: str, text: str, timeout: float = 5.0) -> str | None:
    """Best-effort command. Returns None instead of raising — used for chat notices,
    where a failed announcement must never take down the watcher."""
    if not password:
        return None
    try:
        with RconClient(host, port, password, timeout) as client:
            return client.command(text)
    except (OSError, RconError):
        return None
