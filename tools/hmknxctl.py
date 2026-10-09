#!/usr/bin/env python3
"""
hmknxctl — IBBConfig replacement for the Hörmann/Ing.-Budde HM-KNX gateway.

Talks the standard KNX network management protocol over a KNXnet/IP tunnel.
No Falcon, no Windows. Configuration is stored as YAML.

Subcommands:
    identify      Print device descriptor (mfg, serial, order, app version)
    find          List devices currently in programming mode
    read          Read full config from device, output YAML (or save to file)
    write         Write config from YAML to device (partial diff by default)
    set-pa        Program a new physical address (target must be in prog mode)
    progmode      Toggle the device's programming mode
    restart       Soft-restart the device
    dump-raw      Hex-dump address table + object table (diagnostic)

Wire-protocol quirks of HM-KNX firmware (verified on HM2KNX, app version 5):
    * Standard descriptor properties (IO 0/3) require xknx start_index=1.
    * Homebrew tables on IO 10 (AddrTable) and IO 11 (ObjTable) require
      start_index=0 (Falcon-style 0-based indexing). start_index=1 elicits
      a malformed 1-byte TPDU response.
    * Address table entry: 3 bytes [addr_hi, addr_lo, ko_index] at
      IO=10 prop=table_index. Index 0 holds own PA with ko=0. End-marker:
      GA 0/0/0 with ko=0.
    * Object table entry: 1 byte = flags (C=0x01,R=0x02,W=0x04,T=0x08,
      U=0x20,I=0x40) at IO=11 prop=ko_index.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from xknx import XKNX
from xknx.exceptions import ConversionError
from xknx.io import ConnectionConfig, ConnectionType
from xknx.management.procedures import (
    nm_individual_address_read,
    nm_individual_address_write,
)
from xknx.telegram import IndividualAddress
from xknx.telegram import apci as A


# =============================================================================
# xknx 3.15 patch: APCI.from_knx raises IndexError on too-short raw, dropping
# entire frames. Convert to ConversionError so xknx's own error path handles it.
# =============================================================================
_orig_apci_from_knx = A.APCI.from_knx.__func__


@classmethod
def _safe_apci_from_knx(cls, raw: bytes):
    if len(raw) < 2:
        raise ConversionError(f"APCI raw too short: {raw!r}")
    return _orig_apci_from_knx(cls, raw)


A.APCI.from_knx = _safe_apci_from_knx


def _quiet_loop_exc(loop, context):
    if isinstance(context.get("exception"), IndexError):
        return
    loop.default_exception_handler(context)


# =============================================================================
# KO catalog — the device's KO list is firmware-baked, only flags are
# configurable. Source: Device.IdentifyDevice() in IBBConfig.exe.
# =============================================================================
@dataclass(frozen=True)
class KODef:
    index: int
    name: str
    dpt: str
    default_flags: str  # subset of "CRWTUI"
    description: str


KO_CATALOG: dict[tuple[str, int], list[KODef]] = {
    # HM-KNX with newer firmware (app_version != 1) — 15 KOs, S2+S3
    ("HM2KNX", "default"): [
        KODef(1, "Open-Close", "1.009", "CW", "Open or close garage door"),
        KODef(2, "Stop", "1.010", "CW", "Stop garage door if moving"),
        KODef(3, "Vent", "1.001", "CW", "Open garage door for venting"),
        KODef(4, "Light", "1.012", "CW", "Toggles the light"),
        KODef(5, "Drive Lock", "1.001", "CRWI", "Locks the drive (rf-remote and switches)"),
        KODef(6, "KNX Lock", "1.001", "CRWI", "Locks only the KNX-interface"),
        KODef(7, "Status Open", "1.002", "CRT", "1 if door is completely open"),
        KODef(8, "Status Closed", "1.002", "CRT", "1 if door is completely closed"),
        KODef(9, "Status Venting", "1.002", "CRT", "1 if door is at venting position"),
        KODef(10, "Status Moving", "1.002", "CRT", "1 if door is moving"),
        KODef(11, "Status Moving Up", "1.002", "CRT", "1 if door is moving up (S3)"),
        KODef(12, "Status Moving Down", "1.002", "CRT", "1 if door is moving down (S3)"),
        KODef(13, "Status Pre-Warn", "1.002", "CRT", "1 if door is about to move down (S3)"),
        KODef(14, "Status Light", "1.002", "CRT", "1 if light is switched on"),
        KODef(15, "Status Error", "12.000", "CRWT", "Error code, 0 if no error"),
    ],
    # HM-KNX legacy SupraMatic 2 firmware (app_version == 1) — 11 KOs
    ("HM2KNX", 1): [
        KODef(1, "Open-Close", "1.009", "CW", "Open or close garage door"),
        KODef(2, "Stop", "1.010", "CW", "Stop garage door if moving"),
        KODef(3, "Light", "1.012", "CW", "Toggles the light"),
        KODef(4, "Vent", "1.001", "CW", "Open garage door for venting"),
        KODef(5, "Drive Lock", "1.001", "CRWI", "Locks the drive"),
        KODef(6, "KNX Lock", "1.001", "CRWI", "Locks only the KNX-interface"),
        KODef(7, "Status Open", "1.002", "CRT", "1 if door is completely open"),
        KODef(8, "Status Closed", "1.002", "CRT", "1 if door is completely closed"),
        KODef(9, "Status Moving", "1.002", "CRT", "1 if door is moving"),
        KODef(10, "Status Vent", "1.002", "CRT", "1 if door is at vent position"),
        KODef(11, "Status Error", "12.000", "CRWT", "Error code, 0 if no error"),
    ],
    # SVP2KNX is a different Hörmann product (entrance latch) — 6 KOs.
    ("SVP2KNX", "default"): [
        KODef(1, "Unlock (Timed)", "1.003", "CRWT", "Unlock for 10s (cancelable)"),
        KODef(2, "Daytime Latch", "1.001", "CRWT", "Unlock permanently"),
        KODef(3, "Status Closed", "1.002", "CRT", "1 if door is closed"),
        KODef(4, "Status Locked", "1.002", "CRT", "1 if door is locked"),
        KODef(5, "Handle Pressed", "1.002", "CRT", "1 if handle is pressed"),
        KODef(6, "Status Error", "12.000", "CRWT", "Error code, 0 if no error"),
    ],
}


def lookup_kos(order: str, app_version: int) -> list[KODef]:
    if order == "HM2KNX" and app_version == 1:
        return KO_CATALOG[("HM2KNX", 1)]
    if order == "HM2KNX":
        return KO_CATALOG[("HM2KNX", "default")]
    if order == "SVP2KNX":
        return KO_CATALOG[("SVP2KNX", "default")]
    raise ValueError(f"Unknown device order info {order!r} — only HM2KNX/SVP2KNX known")


# =============================================================================
# Flag byte ↔ string conversion ("CRWTUI" syntax compatible with IBBConfig)
# =============================================================================
FLAG_BITS = {"C": 0x01, "R": 0x02, "W": 0x04, "T": 0x08, "U": 0x20, "I": 0x40}
FLAG_ORDER = "CRWTUI"


def flags_to_byte(flags: str) -> int:
    b = 0
    for ch in flags.upper():
        if ch == " " or ch == "":
            continue
        if ch not in FLAG_BITS:
            raise ValueError(f"Unknown flag character {ch!r}; allowed: {''.join(FLAG_BITS)}")
        b |= FLAG_BITS[ch]
    return b


def byte_to_flags(b: int) -> str:
    return "".join(ch for ch in FLAG_ORDER if b & FLAG_BITS[ch])


# =============================================================================
# Address en/decoding (KNX 3-level)
# =============================================================================
def parse_ga(s: str) -> int:
    parts = s.split("/")
    if len(parts) != 3:
        raise ValueError(f"Group address must be M/M/S: {s!r}")
    main, mid, sub = (int(p) for p in parts)
    if not (0 <= main <= 31 and 0 <= mid <= 7 and 0 <= sub <= 255):
        raise ValueError(f"Group address out of range: {s!r}")
    return (main << 11) | (mid << 8) | sub


def fmt_ga(v: int) -> str:
    return f"{v >> 11}/{(v >> 8) & 7}/{v & 0xFF}"


def parse_pa(s: str) -> int:
    parts = s.split(".")
    if len(parts) != 3:
        raise ValueError(f"Physical address must be A.L.D: {s!r}")
    a, l, d = (int(p) for p in parts)
    if not (0 <= a <= 15 and 0 <= l <= 15 and 0 <= d <= 255):
        raise ValueError(f"Physical address out of range: {s!r}")
    return (a << 12) | (l << 8) | d


def fmt_pa(v: int) -> str:
    return f"{v >> 12}.{(v >> 8) & 0xF}.{v & 0xFF}"


# =============================================================================
# Low-level wire helpers — one connection per operation (avoids xknx's
# brittle TPCI sequence-number tracking when many requests run back-to-back).
# =============================================================================
async def prop_read(
    xknx_inst: XKNX,
    dest: IndividualAddress,
    obj: int,
    pid: int,
    *,
    count: int = 1,
    start: int = 0,
    timeout: float = 3.0,
) -> A.PropertyValueResponse | None:
    try:
        async with xknx_inst.management.connection(address=dest) as conn:
            tg = await asyncio.wait_for(
                conn.request(
                    A.PropertyValueRead(
                        object_index=obj, property_id=pid, count=count, start_index=start
                    ),
                    expected=A.PropertyValueResponse,
                ),
                timeout=timeout,
            )
        return tg.payload
    except (asyncio.TimeoutError, Exception):
        return None


async def prop_write(
    xknx_inst: XKNX,
    dest: IndividualAddress,
    obj: int,
    pid: int,
    data: bytes,
    *,
    start: int = 0,
    timeout: float = 3.0,
) -> bool:
    """xknx 3.15 has no PropertyValueWrite request/response helper at the
    P2P level for our purposes (no response expected on Write per spec — the
    transport-layer T_ACK is the only confirmation). We emit the APCI as a
    fire-and-forget on the connection-oriented session."""
    from xknx.telegram import Telegram
    from xknx.telegram.tpci import TDataConnected

    count = len(data)
    payload = A.PropertyValueWrite(
        object_index=obj, property_id=pid, count=count, start_index=start, data=data
    )
    try:
        async with xknx_inst.management.connection(address=dest) as conn:
            tg = Telegram(
                destination_address=dest,
                source_address=xknx_inst.current_address,
                tpci=TDataConnected(sequence_number=next(conn.sequence_number)),
                payload=payload,
            )
            await xknx_inst.cemi_handler.send_telegram(tg)
            # Wait briefly for the device's TAck so the connection isn't torn
            # down with the request still in flight.
            await asyncio.sleep(0.15)
        return True
    except Exception as exc:
        logging.error("PropertyWrite IO=%d PID=%d failed: %s", obj, pid, exc)
        return False


async def restart(xknx_inst: XKNX, dest: IndividualAddress, *, timeout: float = 3.0) -> bool:
    from xknx.telegram import Telegram
    from xknx.telegram.tpci import TConnect, TDisconnect

    try:
        async with xknx_inst.management.connection(address=dest):
            tg = Telegram(
                destination_address=dest,
                source_address=xknx_inst.current_address,
                tpci=__import__("xknx.telegram.tpci", fromlist=["TDataConnected"]).TDataConnected(
                    sequence_number=0
                ),
                payload=A.Restart(),
            )
            await xknx_inst.cemi_handler.send_telegram(tg)
            await asyncio.sleep(0.3)
        return True
    except Exception as exc:
        logging.error("Restart failed: %s", exc)
        return False


async def progmode_toggle(xknx_inst: XKNX, dest: IndividualAddress) -> bool:
    """Toggle BCU2 prog-mode via Memory_Read+XOR+Memory_Write at addr 0x60.
    Bit 0x81 — exactly what IBBConfig.BusAccessFalcon.ProgmodeToggle does."""
    from xknx.telegram import Telegram
    from xknx.telegram.tpci import TDataConnected

    try:
        async with xknx_inst.management.connection(address=dest) as conn:
            mr = await asyncio.wait_for(
                conn.request(A.MemoryRead(address=0x60, count=1), expected=A.MemoryResponse),
                timeout=3.0,
            )
            cur = mr.payload.data[0]
            new = cur ^ 0x81
            tg = Telegram(
                destination_address=dest,
                source_address=xknx_inst.current_address,
                tpci=TDataConnected(sequence_number=next(conn.sequence_number)),
                payload=A.MemoryWrite(address=0x60, count=1, data=bytes([new])),
            )
            await xknx_inst.cemi_handler.send_telegram(tg)
            await asyncio.sleep(0.2)
            print(f"  Memory[0x60]: 0x{cur:02x} -> 0x{new:02x}")
        return True
    except Exception as exc:
        logging.error("ProgMode toggle failed: %s", exc)
        return False


# =============================================================================
# High-level operations
# =============================================================================
async def op_identify(xknx_inst: XKNX, dest: IndividualAddress) -> dict:
    """Read standard KNX descriptor properties (use start_index=1 — KNX-spec)."""
    info: dict = {"pa": fmt_pa(dest.raw)}
    spec = [
        ("manufacturer_id", 0, 12),
        ("serial", 0, 11),
        ("order", 0, 15),
        ("app_version", 3, 13),
    ]
    for key, obj, pid in spec:
        resp = await prop_read(xknx_inst, dest, obj, pid, start=1)
        if resp is None or resp.count == 0:
            info[key] = None
            continue
        info[key] = bytes(resp.data)
    out = {"pa": info["pa"]}
    if info["manufacturer_id"]:
        out["manufacturer_id"] = int.from_bytes(info["manufacturer_id"], "big")
    if info["serial"]:
        out["serial"] = info["serial"].hex(":")
    if info["order"]:
        out["order"] = info["order"].rstrip(b"\x00").decode("ascii", errors="replace")
    if info["app_version"]:
        out["app_version"] = info["app_version"][-1]  # last byte = version per IBBConfig
        out["app_version_raw"] = info["app_version"].hex()
    return out


async def op_read_config(
    xknx_inst: XKNX, dest: IndividualAddress, ident: dict | None = None
) -> dict:
    """Read full configuration (PA, KOs+flags, GA mappings) from the device."""
    if ident is None:
        ident = await op_identify(xknx_inst, dest)
    if not ident.get("order"):
        raise RuntimeError("Could not identify device (order info missing)")

    kos = lookup_kos(ident["order"], ident.get("app_version") or 0)

    # 1) KO flag table — IO 11, prop=ko_index, start=0
    flag_by_index: dict[int, int] = {}
    for ko in kos:
        resp = await prop_read(xknx_inst, dest, 11, ko.index, start=0)
        if resp is None or resp.count == 0 or not resp.data:
            logging.warning("Could not read flags for KO %d (%s)", ko.index, ko.name)
            continue
        flag_by_index[ko.index] = resp.data[0]

    # 2) Address table — IO 10, prop=index 0..255, start=0; stop on end-marker
    ga_by_ko: dict[int, list[int]] = {}
    own_pa: int | None = None
    for i in range(256):
        resp = await prop_read(xknx_inst, dest, 10, i, start=0)
        if resp is None or resp.count == 0 or not resp.data:
            break
        d = resp.data
        if len(d) < 3:
            break
        addr = (d[0] << 8) | d[1]
        ko_index = d[2]
        if i == 0:
            own_pa = addr  # PA at index 0
            continue
        if addr == 0 and ko_index == 0:
            break  # end marker
        if ko_index == 0:
            continue  # malformed entry, skip
        ga_by_ko.setdefault(ko_index, []).append(addr)

    cfg = {
        "device": {
            "pa": fmt_pa(own_pa) if own_pa is not None else ident["pa"],
            "order": ident.get("order"),
            "app_version": ident.get("app_version"),
            "manufacturer_id": f"0x{ident.get('manufacturer_id', 0):04x}",
            "serial": ident.get("serial"),
        },
        "communication_objects": [
            {
                "id": ko.index,
                "name": ko.name,
                "dpt": ko.dpt,
                "flags": byte_to_flags(flag_by_index.get(ko.index, flags_to_byte(ko.default_flags))),
                "group_addresses": [fmt_ga(g) for g in ga_by_ko.get(ko.index, [])],
                "description": ko.description,
            }
            for ko in kos
        ],
    }
    return cfg


async def op_write_config(
    xknx_inst: XKNX,
    dest: IndividualAddress,
    cfg: dict,
    *,
    full: bool = False,
    dry_run: bool = False,
) -> int:
    """Write KO flags + address table to device, partial-update style by default."""
    ident = await op_identify(xknx_inst, dest)
    if not ident.get("order"):
        raise RuntimeError("Could not identify device — refusing to write")

    if cfg["device"].get("order") and cfg["device"]["order"] != ident["order"]:
        raise RuntimeError(
            f"Config is for {cfg['device']['order']!r} but device reports "
            f"{ident['order']!r}. Refusing."
        )
    kos = lookup_kos(ident["order"], ident.get("app_version") or 0)
    ko_by_id = {k.index: k for k in kos}

    # Read current state (for diff)
    current = await op_read_config(xknx_inst, dest, ident=ident)
    cur_flags = {co["id"]: flags_to_byte(co["flags"]) for co in current["communication_objects"]}
    cur_gas = {co["id"]: list(co["group_addresses"]) for co in current["communication_objects"]}

    # Plan KO-flag writes
    flag_writes: list[tuple[int, int]] = []  # (ko_index, new_byte)
    for co in cfg["communication_objects"]:
        idx = int(co["id"])
        if idx not in ko_by_id:
            raise ValueError(f"Unknown KO id {idx} for this firmware")
        new_b = flags_to_byte(co.get("flags", ko_by_id[idx].default_flags))
        if full or cur_flags.get(idx) != new_b:
            flag_writes.append((idx, new_b))

    # Plan address-table — entry 0 is own PA (don't touch via this path; use set-pa)
    new_addr_entries: list[tuple[int, int]] = []  # (addr_value, ko_index)
    for co in cfg["communication_objects"]:
        idx = int(co["id"])
        for ga in co.get("group_addresses", []) or []:
            new_addr_entries.append((parse_ga(ga), idx))
    # Deduplicate while preserving order
    seen: set[tuple[int, int]] = set()
    deduped = []
    for e in new_addr_entries:
        if e in seen:
            continue
        seen.add(e)
        deduped.append(e)
    new_addr_entries = deduped

    # Compare to current address table layout (rebuild from cur_gas in same KO-id order)
    cur_addr_entries: list[tuple[int, int]] = []
    for co in current["communication_objects"]:
        for ga in co["group_addresses"]:
            cur_addr_entries.append((parse_ga(ga), int(co["id"])))

    addr_writes: list[tuple[int, int, int]] = []  # (table_index, addr_value, ko_index)
    write_endmarker_at: int | None = None
    if full or new_addr_entries != cur_addr_entries:
        # Index 0 is own PA — preserve. Entries start at index 1.
        for i, (addr, ko) in enumerate(new_addr_entries, start=1):
            if (
                full
                or i - 1 >= len(cur_addr_entries)
                or cur_addr_entries[i - 1] != (addr, ko)
            ):
                addr_writes.append((i, addr, ko))
        if len(new_addr_entries) != len(cur_addr_entries):
            write_endmarker_at = 1 + len(new_addr_entries)

    print(f"# Plan: {len(flag_writes)} KO flag updates, {len(addr_writes)} address-table updates"
          + (f", end-marker at index {write_endmarker_at}" if write_endmarker_at is not None else ""))
    if not flag_writes and not addr_writes and write_endmarker_at is None:
        print("# Nothing to do — device already matches config.")
        return 0

    if dry_run:
        for idx, b in flag_writes:
            print(f"  would write IO=11 prop={idx} flags=0x{b:02x} ({byte_to_flags(b)})")
        for ti, a, ko in addr_writes:
            print(f"  would write IO=10 prop={ti} addr={fmt_ga(a)} ko={ko}")
        if write_endmarker_at is not None:
            print(f"  would write IO=10 prop={write_endmarker_at} end-marker (00 00 00)")
        return 0

    failures = 0
    # 1) KO flags
    for idx, b in flag_writes:
        ok = await prop_write(xknx_inst, dest, 11, idx, bytes([b]), start=0)
        print(f"  IO=11 prop={idx:2d} flags=0x{b:02x} ({byte_to_flags(b):6s})  -> {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1
    # 2) Address-table entries
    for ti, addr, ko in addr_writes:
        data = bytes([(addr >> 8) & 0xFF, addr & 0xFF, ko & 0xFF])
        ok = await prop_write(xknx_inst, dest, 10, ti, data, start=0)
        print(
            f"  IO=10 prop={ti:2d} addr={fmt_ga(addr):>9s} ko={ko:2d}  -> {'OK' if ok else 'FAIL'}"
        )
        failures += 0 if ok else 1
    # 3) End marker (if shrinking or growing)
    if write_endmarker_at is not None:
        ok = await prop_write(xknx_inst, dest, 10, write_endmarker_at, b"\x00\x00\x00", start=0)
        print(f"  IO=10 prop={write_endmarker_at:2d} end-marker      -> {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1
    return failures


# =============================================================================
# Setup XKNX + CLI dispatch
# =============================================================================
async def with_xknx(gateway_ip: str, verbose: bool, fn):
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s.%(msecs)03d %(name)-26s %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")

    asyncio.get_event_loop().set_exception_handler(_quiet_loop_exc)

    cfg = ConnectionConfig(connection_type=ConnectionType.TUNNELING, gateway_ip=gateway_ip)
    xknx_inst = XKNX(connection_config=cfg)
    await xknx_inst.start()
    try:
        return await fn(xknx_inst)
    finally:
        await xknx_inst.stop()


def yaml_dump(obj, stream=None):
    return yaml.safe_dump(obj, stream, sort_keys=False, allow_unicode=True, default_flow_style=False)


def cmd_identify(args):
    async def go(xknx_inst):
        info = await op_identify(xknx_inst, IndividualAddress(args.device))
        print(yaml_dump(info), end="")

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def cmd_find(args):
    async def go(xknx_inst):
        addrs = await nm_individual_address_read(xknx_inst, timeout=3, raise_if_multiple=False)
        if not addrs:
            print("# No device in programming mode")
            return
        for a in addrs:
            print(fmt_pa(int(a)))

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def cmd_read(args):
    async def go(xknx_inst):
        cfg = await op_read_config(xknx_inst, IndividualAddress(args.device))
        text = yaml_dump(cfg)
        if args.output and args.output != "-":
            Path(args.output).write_text(text)
            print(f"# Wrote {args.output}")
        else:
            print(text, end="")

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def cmd_write(args):
    async def go(xknx_inst):
        text = sys.stdin.read() if args.config == "-" else Path(args.config).read_text()
        cfg = yaml.safe_load(text)
        if not cfg or "communication_objects" not in cfg:
            sys.exit("Config file is missing 'communication_objects' section")
        rc = await op_write_config(
            xknx_inst,
            IndividualAddress(args.device),
            cfg,
            full=args.full,
            dry_run=args.dry_run,
        )
        sys.exit(0 if rc == 0 else 1)

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def cmd_set_pa(args):
    async def go(xknx_inst):
        new_pa = IndividualAddress(args.new_pa)
        # safety: device must be in prog mode
        addrs = await nm_individual_address_read(xknx_inst, timeout=3, raise_if_multiple=False)
        if len(addrs) == 0:
            sys.exit("No device in programming mode. Press the prog-mode button first.")
        if len(addrs) > 1:
            sys.exit(f"Multiple devices in prog mode: {addrs}. Refusing for safety.")
        print(f"# Found {addrs[0]} in prog mode -> {new_pa}")
        if not args.yes:
            if input("Proceed? [y/N] ").strip().lower() != "y":
                sys.exit("aborted")
        await nm_individual_address_write(xknx_inst, new_pa)
        print(f"# Wrote new PA {new_pa}")

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def cmd_progmode(args):
    async def go(xknx_inst):
        ok = await progmode_toggle(xknx_inst, IndividualAddress(args.device))
        sys.exit(0 if ok else 1)

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def cmd_restart(args):
    async def go(xknx_inst):
        ok = await restart(xknx_inst, IndividualAddress(args.device))
        sys.exit(0 if ok else 1)

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def cmd_dump_raw(args):
    async def go(xknx_inst):
        dest = IndividualAddress(args.device)
        print("# Address Table (IO=10):")
        for i in range(256):
            r = await prop_read(xknx_inst, dest, 10, i, start=0, timeout=2.0)
            if r is None or r.count == 0 or not r.data:
                print(f"  [{i:3d}] <end>")
                break
            d = r.data
            if len(d) >= 3:
                addr = (d[0] << 8) | d[1]
                ko = d[2]
                if i == 0:
                    print(f"  [{i:3d}] PA  {fmt_pa(addr)}    ko={ko}  raw={d.hex(' ')}")
                else:
                    is_end = addr == 0 and ko == 0
                    print(f"  [{i:3d}] {'END' if is_end else 'GA '} {fmt_ga(addr):>9s}    ko={ko:2d}  raw={d.hex(' ')}"
                          + ("  <end-marker>" if is_end else ""))
                    if is_end:
                        break
            else:
                print(f"  [{i:3d}] short data: {d.hex(' ')}")
                break

        print("# Object Table (IO=11):")
        for i in range(0, 17):
            r = await prop_read(xknx_inst, dest, 11, i, start=0, timeout=2.0)
            if r is None or r.count == 0 or not r.data:
                print(f"  [KO={i:2d}] <not implemented>")
                continue
            b = r.data[0]
            print(f"  [KO={i:2d}] flags=0x{b:02x} ({byte_to_flags(b):6s})  raw={r.data.hex(' ')}")

    asyncio.run(with_xknx(args.gateway, args.verbose, go))


def main():
    ap = argparse.ArgumentParser(prog="hmknxctl", description=__doc__.split("\n\n")[0])
    ap.add_argument("-g", "--gateway", required=True, help="KNX/IP interface IP, e.g. 192.168.1.10")
    ap.add_argument("-d", "--device", default=None,
                    help="Target HM-KNX physical address, e.g. 1.1.5 (required except for 'find')")
    ap.add_argument("-v", "--verbose", action="store_true")
    sp = ap.add_subparsers(dest="cmd", required=True)

    sp.add_parser("identify", help="Read device descriptor").set_defaults(func=cmd_identify)
    sp.add_parser("find", help="List devices in programming mode").set_defaults(func=cmd_find)

    p_read = sp.add_parser("read", help="Read full config to YAML")
    p_read.add_argument("-o", "--output", default="-", help="Output file (default stdout)")
    p_read.set_defaults(func=cmd_read)

    p_write = sp.add_parser("write", help="Write config from YAML to device")
    p_write.add_argument("-c", "--config", required=True, help="YAML config file (or - for stdin)")
    p_write.add_argument("--full", action="store_true", help="Full overwrite, no diff")
    p_write.add_argument("--dry-run", action="store_true", help="Plan only, do not write")
    p_write.set_defaults(func=cmd_write)

    p_setpa = sp.add_parser("set-pa", help="Program a new physical address")
    p_setpa.add_argument("new_pa")
    p_setpa.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    p_setpa.set_defaults(func=cmd_set_pa)

    sp.add_parser("progmode", help="Toggle programming mode").set_defaults(func=cmd_progmode)
    sp.add_parser("restart", help="Soft-restart device").set_defaults(func=cmd_restart)
    sp.add_parser("dump-raw", help="Hex dump address+object tables").set_defaults(func=cmd_dump_raw)

    args = ap.parse_args()
    if args.cmd != "find" and not args.device:
        ap.error(f"-d/--device is required for '{args.cmd}'")
    args.func(args)


if __name__ == "__main__":
    main()
