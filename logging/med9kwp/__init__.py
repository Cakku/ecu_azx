"""med9kwp -- VW TP2.0 / KWP2000 client (and server) for the MED9.1.1 ECU.

Three layers, each usable on its own:

``can_transport``
    turns a small config into a `python-can` bus and wraps it in a `CanLink`
    with a blocking `recv(timeout)` and a non-blocking `send()`.
``tp20``
    VW Transport Protocol 2.0: channel setup on 0x200, parameter exchange,
    segmentation, ACK handling, keep-alive, disconnect.  Client **and**
    server, so `logging/ecu_sim.py` can speak the same protocol back.
``kwp``
    KWP2000 on top of TP2.0: request/response, NRC decoding, diagnostic
    session, TesterPresent, SecurityAccess level 2, DDLI define/read and
    RequestUpload/TransferData.

Nothing here uses threads: every wait is a poll with a deadline, so the whole
stack can be driven from one loop (`logging/med9log.py`) or embedded in one
(the simulator's `serve_forever`).

The protocol facts come from public write-ups of TP2.0 (see `logging/README.md`
section 5) and, for KWP, from `re/findings/kwp.md`, which was produced from
this ECU's own code.  No third-party code is vendored; see `NOTICE.md`.
"""
from __future__ import annotations

from .can_transport import BusConfig, CanLink, open_link, parse_bus_spec
from .kwp import (
    NRC_NAMES,
    KwpClient,
    KwpError,
    NegativeResponse,
    key_level1,
    key_level2,
)
from .tp20 import (
    CHANNEL_SETUP_ID,
    Tp20Client,
    Tp20Error,
    Tp20Params,
    Tp20Server,
    Tp20Timeout,
    decode_timing,
    encode_timing,
)

__all__ = [
    "BusConfig", "CanLink", "open_link", "parse_bus_spec",
    "CHANNEL_SETUP_ID", "Tp20Client", "Tp20Server", "Tp20Params",
    "Tp20Error", "Tp20Timeout", "decode_timing", "encode_timing",
    "KwpClient", "KwpError", "NegativeResponse", "NRC_NAMES",
    "key_level1", "key_level2",
]
