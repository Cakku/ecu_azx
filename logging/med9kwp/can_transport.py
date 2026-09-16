"""python-can bus plumbing for the MED9 logger.

One place decides which adapter is used, so `tp20.py` and `kwp.py` never
import `can` themselves and stay unit-testable without hardware.

The ECU's powertrain CAN runs at **500 kbit/s** (`re/findings/can.md` section 3:
PRESDIV 6 / PROPSEG 7 / PSEG1 3 / PSEG2 2 at f_sys 56 MHz = 16 Tq, sample point
81.25 %), so 500000 is the default and the only bitrate that makes sense.

This Mac has no SocketCAN (`docs/03_tooling.md` section 6).  The supported
routes are

    virtual:med9                      in-process test bus (no hardware)
    gs_usb:0                          candleLight / CANable in gs_usb mode
    slcan:/dev/tty.usbmodemXXXX       CANable in slcan (Lawicel) mode
    socketcand:pi.local:29536:can0    the Raspberry Pi in pi_can_setup/
    socketcan:can0                    a Linux host

Usage::

    from med9kwp import open_link, parse_bus_spec
    link = open_link(parse_bus_spec("virtual:med9"))
    link.send(0x200, b"\\x01\\xc0\\x00\\x10\\x00\\x03\\x01")
    frame = link.recv(0.2)          # (can_id, data) or None
    link.close()
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

DEFAULT_BITRATE = 500_000
DEFAULT_SPEC = "virtual:med9"


class CanUnavailable(RuntimeError):
    """python-can is not installed, or the adapter could not be opened."""


@dataclass
class BusConfig:
    """Everything `can.Bus(...)` needs, in one serialisable object."""

    interface: str = "virtual"
    channel: str | int = "med9"
    bitrate: int = DEFAULT_BITRATE
    extra: dict = field(default_factory=dict)

    def kwargs(self) -> dict:
        kw: dict = {"interface": self.interface, "channel": self.channel}
        # The virtual bus has no wire, so a bitrate is meaningless (and newer
        # python-can warns about it).
        if self.interface != "virtual":
            kw["bitrate"] = self.bitrate
        kw.update(self.extra)
        return kw

    def describe(self) -> str:
        if self.interface == "virtual":
            return f"virtual:{self.channel}"
        return f"{self.interface}:{self.channel} @ {self.bitrate // 1000} kbit/s"


def parse_bus_spec(spec: str | None) -> BusConfig:
    """Parse ``interface:channel[:more]`` into a :class:`BusConfig`.

    ``socketcand`` takes ``socketcand:<host>:<port>:<channel>``; every other
    interface takes ``<interface>:<channel>``.  ``None`` or ``""`` gives the
    in-process virtual bus, which is what the tests and ``--sim`` use.
    """
    if not spec:
        spec = DEFAULT_SPEC
    parts = spec.split(":")
    iface = parts[0]
    if iface == "socketcand":
        if len(parts) < 4:
            raise ValueError(
                "socketcand needs socketcand:<host>:<port>:<channel>, "
                f"got {spec!r}")
        return BusConfig(interface="socketcand", channel=parts[3],
                         extra={"host": parts[1], "port": int(parts[2])})
    channel: str | int = ":".join(parts[1:]) if len(parts) > 1 else "med9"
    if iface == "gs_usb":
        # gs_usb addresses the adapter by index, and python-can wants both
        # `channel` and `index` for it.
        idx = int(channel) if str(channel).isdigit() else 0
        return BusConfig(interface="gs_usb", channel=idx, extra={"index": idx})
    return BusConfig(interface=iface, channel=channel)


class CanLink:
    """A bus plus the two operations the protocol layers need.

    `recv` returns ``(can_id, data)`` or ``None`` on timeout; extended-id and
    remote/error frames are dropped, because TP2.0 uses neither.  An optional
    `accept` set filters to the ids of one channel so a busy bus does not make
    the protocol layer do the filtering.
    """

    def __init__(self, bus, *, accept: Iterable[int] | None = None,
                 describe: str = "?"):
        self.bus = bus
        self.accept: set[int] | None = set(accept) if accept is not None else None
        self.description = describe
        self.tx_count = 0
        self.rx_count = 0

    # -- filtering ---------------------------------------------------------
    def set_accept(self, ids: Iterable[int] | None) -> None:
        self.accept = set(ids) if ids is not None else None

    # -- io ----------------------------------------------------------------
    def send(self, can_id: int, data: bytes) -> None:
        import can
        self.bus.send(can.Message(arbitration_id=can_id, data=bytes(data),
                                  is_extended_id=False))
        self.tx_count += 1

    def recv(self, timeout: float) -> tuple[int, bytes] | None:
        """Wait up to `timeout` seconds for one accepted standard frame."""
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            left = deadline - time.monotonic()
            msg = self.bus.recv(max(left, 0.0))
            if msg is None:
                return None
            if msg.is_extended_id or msg.is_remote_frame or msg.is_error_frame:
                pass
            elif self.accept is None or msg.arbitration_id in self.accept:
                self.rx_count += 1
                return msg.arbitration_id, bytes(msg.data)
            if time.monotonic() >= deadline:
                return None

    def drain(self) -> None:
        """Throw away everything already queued."""
        while self.bus.recv(0.0) is not None:
            pass

    def close(self) -> None:
        try:
            self.bus.shutdown()
        except Exception:                                  # pragma: no cover
            pass

    def __enter__(self) -> "CanLink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def have_can() -> bool:
    """True if python-can can be imported (tests skip cleanly when it cannot)."""
    try:
        import can  # noqa: F401
    except Exception:
        return False
    return True


def open_link(cfg: BusConfig, *, accept: Iterable[int] | None = None) -> CanLink:
    """Open `cfg` and wrap it. Raises :class:`CanUnavailable` with advice."""
    try:
        import can
    except Exception as exc:                               # pragma: no cover
        raise CanUnavailable(
            "python-can is not installed; `pip install -r requirements.txt`"
        ) from exc
    try:
        bus = can.Bus(**cfg.kwargs())
    except Exception as exc:
        raise CanUnavailable(
            f"could not open {cfg.describe()}: {exc}\n"
            "  gs_usb/candleLight: needs the optional backend --\n"
            "      brew install libusb && pip install 'python-can[gs-usb]'\n"
            "    then check the adapter is plugged in (`system_profiler "
            "SPUSBDataType | grep -i can`)\n"
            "  slcan: needs pyserial (`pip install pyserial`); the device node "
            "is /dev/tty.usbmodem*, not /dev/cu.*\n"
            "  socketcand: needs no extra package -- is socketcand running on "
            "the Pi? (pi_can_setup/README.md section 4)\n"
            "  no hardware at all: use `--sim`, which runs logging/ecu_sim.py "
            "in this process (the `virtual` bus does not cross processes)."
        ) from exc
    return CanLink(bus, accept=accept, describe=cfg.describe())
