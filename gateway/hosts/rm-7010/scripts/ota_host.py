#!/usr/bin/env python3
"""ota_host.py -- PC-side host tool for the N32G455REL7 CAN-OTA protocol.

This script is the host half of the CAN-OTA protocol implemented by
ota/ota_proto.c.  The device half lives in the bootloader (boot/main.c) and in
the application (src/main.c); both share ota/ota_layout.h with this tool, so the
constants below are copied from that header and must be kept in sync with it.

Only python-can is required (import can).  Python 3.8+.


1. CAN LAYER
------------
  Classic CAN 2.0A, standard 11-bit identifiers, 8 data bytes per frame.
  Extended identifiers are never used.

    host -> device : 0x600 + node
    device -> host : 0x580 + node

  Byte 0 of every payload is the command byte.  The device only accepts frames
  whose standard id equals 0x600 + node and whose DLC is at least 1.  Replies
  are matched on both the arbitration id and the command byte, so a stale or
  unrelated frame can never be mistaken for a reply to the command we sent.


2. FRAME LAYOUTS (data[0] = command, data[1..7] = payload, rest zero)
--------------------------------------------------------------------
  Host -> device
    0x01 GET_INFO      no payload (all 8 bytes zero)
    0x02 ENTER_BOOT    no payload
    0x03 START_UPDATE  [1] slot, [2..4] image size LE24, [5..6] version LE16,
                       [7] flags (0)
    0x04 DATA          [1..7] 7 image bytes.  The last frame of an image is
                       padded with 0xFF; the device discards the bytes beyond
                       the declared image size.
    0x05 SYNC          [1..2] number of DATA frames in this block LE16,
                       [3..5] byte offset at the end of this block LE24
    0x06 END           [1..4] CRC32 (zlib) of the whole image LE32
    0x07 COMMIT        no payload.  Writes metadata and reboots.
    0x08 ROLLBACK      no payload.  Switches to the other slot and reboots.
    0x09 ABORT         no payload.  Gives up on the current transfer.
    0x0A RESET         no payload.  Soft reset.

  Device -> host
    0x81 ACK           [1..4] confirmed byte offset LE32
    0x82 NACK          [1..4] SAFE RETRANSMIT offset LE32 (every byte below it
                       is already correct on the device), [5] error code
    0x83 INFO          [1] active slot (0=A, 1=B), [2] inactive slot,
                       [3] state (0=boot/idle, 1=app, 2=receiving,
                       3=verifying), [4] try_left, [5..6] version of the
                       inactive slot LE16, [7] flags: bit0 valid_A,
                       bit1 valid_B, bit2 pending

  NACK error codes: 1 bad command, 2 bad argument, 3 wrong state,
  4 flash error, 5 CRC mismatch, 6 size mismatch, 7 frame count mismatch,
  8 invalid or forbidden slot.


3. BLOCK / SYNC FLOW (why the transfer is not a plain byte stream)
-----------------------------------------------------------------
  CAN only has a 3 frame receive FIFO and erasing one 2 KiB flash page takes
  tens of milliseconds.  Blasting frames therefore always loses some.  The
  protocol instead works in blocks:

      host  : [up to 292 DATA frames] -> SYNC(block frame count, end offset)
      device: write whole pages, count received frames, compare on SYNC

  292 frames * 7 bytes = 2044 bytes, just under one flash page.  When the frame
  count matches, the device ACKs with the total number of bytes it has received
  and the host continues with the next block.  The confirmed offset in the ACK
  is authoritative: the host always resumes at the offset the device reports.

  The device erases and programs flash ONLY while handling a SYNC, never in the
  middle of a block, so no DATA frame is ever received while the flash is busy.
  A SYNC reply is therefore much slower than every other reply: up to about
  150 ms when two 2 KiB pages have to be erased and programmed.  The host uses a
  dedicated 0.5 s timeout for SYNC (SYNC_TIMEOUT_DEFAULT) while all other
  replies keep the shorter --timeout.

  --page-pause-ms (default 0 = off) inserts a host-side pause after the DATA
  frame that crosses a flash page boundary.  It is only useful for firmware that
  flushes a page in the middle of a block, and it never changes the wire
  protocol: if it is off, or too short, the NACK/retransmit path below still
  recovers.


4. RETRANSMIT SEMANTICS
-----------------------
  On SYNC the device answers:
    * ACK  -> resend nothing, resume at the offset in the ACK.
    * NACK -> the offset in the NACK means exactly: "everything below this
              offset is already correct, in flash or in the device buffer".
              The host seeks its send position back to that offset and continues
              from there; the device has already rolled its own write pointer
              back to the same place.
    * no reply (lost SYNC) -> the host resends the SAME SYNC up to 3 times
              without resending any data, because a lost SYNC does not mean
              lost data.  Only if the SYNC itself keeps failing does the host
              fall back to resending from the last confirmed offset.

  NACK error codes 1, 2, 3 and 8 are terminal: retrying cannot help, the host
  reports the error and stops (it also sends a best-effort ABORT so the device
  marks the half written image invalid).

  Retransmit budget: --retries N limits how many retransmits may happen WITHOUT
  FORWARD PROGRESS.  Any ACK or NACK whose safe offset is beyond the highest
  offset confirmed so far counts as progress and resets the counter.  A 236 KiB
  image legitimately produces one recoverable NACK per flash page boundary on a
  fast/loose link, so a plain cumulative counter would abort every real update;
  the stall counter is what actually detects a device that cannot make progress.

  END (0x06) carries the CRC32 of the whole image and forces the device to flush
  the last partial page and CRC the slot from flash.  A NACK here means the
  image on the device is wrong: the host reports the error code and never
  commits.

  COMMIT (0x07) is the only command that writes metadata.  It does NOT change
  the active slot: it marks the freshly written slot as pending with try_left=3
  and reboots.  The bootloader then boots the pending slot, decrementing
  try_left; the new application promotes itself with ota_confirm() once it is
  running, which is what finally moves active_slot.  So right after a successful
  update the reported active slot may still be the old one for a second or two,
  and if the new image never confirms, the bootloader rolls back to the slot
  that was active before, after try_left reaches zero.

  GET_INFO's active slot is therefore the authoritative answer to "did the
  update stick", and this tool waits (2 s, then polls for up to 6 s) for it.
  With --no-commit the image is only written and CRC-verified on the device;
  no metadata is touched, no reboot happens and the device keeps booting the
  slot it was booting before.


5. EXIT CODES
-------------
  0  success
  1  protocol or device failure (NACK, CRC/verify failure, no progress)
  2  usage or environment failure (bad options, file problem, no CAN interface,
     device did not answer at all)


6. USAGE
--------
  ota_host.py --channel can0 --baud 500000 --node 0 info
  ota_host.py --channel can0 --baud 500000 --node 0 update --bin build/app_slotB.bin --version 0x0101
  ota_host.py --channel can0 --baud 500000 --node 0 update --bin app.bin --version 0x0101 --dry-run
  ota_host.py --channel can0 --baud 500000 --node 0 update --bin app.bin --no-commit
  ota_host.py --channel can0 --baud 500000 --node 0 rollback
  ota_host.py --channel can0 --baud 500000 --node 0 enter-boot
  ota_host.py --channel can0 --baud 500000 --node 0 reset
  ota_host.py --channel can0 --baud 500000 --node 0 abort

  --channel falls back to the environment variable CAN_CHANNEL and --baud to
  CAN_BAUD, so an empty make variable still works:
      make ota-info            ->  --channel can0 --baud 500000 --node 0 info

  --interface / -i selects the python-can backend (slcan, pcan, vector, kvaser,
  ixxat, usb2can, socketcan, virtual, ...) and is passed to python-can as
  interface=<name>.  When it is omitted (and CAN_INTERFACE is unset) no
  interface is passed at all, so python-can picks its own default; only if that
  fails are backends guessed from the channel name probed as well.  If every
  attempt fails the tool prints the list of known backends and exits with 2.
"""

import argparse
import os
import re
import sys
import time
import zlib

try:
    import can
except ImportError as _can_import_error:  # pragma: no cover - depends on host
    can = None
    CAN_IMPORT_ERROR = _can_import_error
else:
    CAN_IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# Protocol constants (mirrors ota/ota_layout.h -- keep in sync)
# ---------------------------------------------------------------------------

ID_HOST_BASE = 0x600          # host -> device id base
ID_DEV_BASE = 0x580           # device -> host id base
CAN_NODE_MAX = 0x7F           # 0x600 + node must stay a valid 11-bit id

CMD_GET_INFO = 0x01
CMD_ENTER_BOOT = 0x02
CMD_START_UPDATE = 0x03
CMD_DATA = 0x04
CMD_SYNC = 0x05
CMD_END = 0x06
CMD_COMMIT = 0x07
CMD_ROLLBACK = 0x08
CMD_ABORT = 0x09
CMD_RESET = 0x0A
CMD_FOC_STAT = 0x0C

RSP_ACK = 0x81
RSP_NACK = 0x82
RSP_INFO = 0x83
RSP_FOC = 0x87

SLOT_A = 0
SLOT_B = 1
SLOT_NONE = 0xFF

STATE_BOOT = 0
STATE_APP = 1
STATE_RECEIVING = 2
STATE_VERIFYING = 3

INFO_F_VALID_A = 0x01
INFO_F_VALID_B = 0x02
INFO_F_PENDING = 0x04

ERR_NONE = 0
ERR_BAD_CMD = 1
ERR_BAD_ARG = 2
ERR_STATE = 3
ERR_FLASH = 4
ERR_CRC = 5
ERR_SIZE = 6
ERR_FRAMES = 7
ERR_SLOT = 8

DATA_BYTES_PER_FRAME = 7      # ota_layout.h: OTA_DATA_BYTES_PER_FRAME
FRAMES_PER_BLOCK = 292        # ota_layout.h: OTA_FRAMES_PER_BLOCK (292 * 7 = 2044)
FLASH_PAGE_SIZE = 2048        # ota_layout.h: OTA_FLASH_PAGE_SIZE
SLOT_SIZE = 236 * 1024        # ota_layout.h: OTA_SLOT_SIZE
IMAGE_MIN = 4096              # ota_layout.h: OTA_APP_IMAGE_MIN (0x1000)
IMAGE_PAD_BYTE = 0xFF         # erased-flash fill value used for frame padding

SYNC_RETRY_MAX = 3            # retries of the SAME SYNC after a timeout
# A SYNC reply is slow: the device erases/programs up to two 2 KiB pages while
# handling it (~150 ms) and never touches flash anywhere else.  Everything else
# (GET_INFO / START_UPDATE / END / COMMIT / ROLLBACK) answers promptly and uses
# the shorter --timeout.
SYNC_TIMEOUT_DEFAULT = 0.5
DEFAULT_BAUD = 500000
# NOTE: there is deliberately NO DEFAULT_VERSION here.
# A hardcoded default (0x0100) silently re-labelled new builds as an old release:
# pressing F8 without OTA_VERSION= flashed 0x0100 on top of 0x0808, and only the
# flashing log revealed it.  The version is a contract between the compiled app
# (OTA_BUILD_VERSION), this tool (--version) and the bootloader metadata, so it is
# now REQUIRED and comes from ota_version.txt via scripts/version.py (see the
# Makefile).  Same reasoning for the device side: see check_version_guard().

# Error codes that no amount of retransmitting can fix.
FATAL_ERRORS = (ERR_BAD_CMD, ERR_BAD_ARG, ERR_STATE, ERR_SLOT)

ERR_TEXT = {
    ERR_NONE: "no error",
    ERR_BAD_CMD: "bad command",
    ERR_BAD_ARG: "bad argument",
    ERR_STATE: "device in wrong state",
    ERR_FLASH: "flash erase/program error",
    ERR_CRC: "image CRC mismatch",
    ERR_SIZE: "image size mismatch",
    ERR_FRAMES: "data frame count mismatch",
    ERR_SLOT: "invalid or forbidden slot",
}

STATE_TEXT = {
    STATE_BOOT: "boot/idle (bootloader)",
    STATE_APP: "app",
    STATE_RECEIVING: "receiving",
    STATE_VERIFYING: "verifying",
}

# python-can backends tried when --interface is not given, in this order.
FALLBACK_INTERFACES = (
    "socketcan",
    "pcan",
    "vector",
    "kvaser",
    "ixxat",
    "usb2can",
    "systec",
    "nican",
    "canalystii",
    "robotell",
    "slcan",
)

VERBOSE = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class HostError(Exception):
    """Failure with a user facing message and a process exit code."""

    exit_code = 1

    def __init__(self, message):
        Exception.__init__(self, message)
        self.message = message


class ProtocolError(HostError):
    """Protocol or device failure -> exit code 1."""

    exit_code = 1


class UsageError(HostError):
    """Bad usage or bad environment -> exit code 2."""

    exit_code = 2


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def u16le(value):
    return bytes((value & 0xFF, (value >> 8) & 0xFF))


def u24le(value):
    return bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF))


def u32le(value):
    return bytes((value & 0xFF, (value >> 8) & 0xFF,
                  (value >> 16) & 0xFF, (value >> 24) & 0xFF))


def get_u16(data, off):
    return data[off] | (data[off + 1] << 8)


def get_u24(data, off):
    return data[off] | (data[off + 1] << 8) | (data[off + 2] << 16)


def get_u32(data, off):
    return (data[off] | (data[off + 1] << 8) |
            (data[off + 2] << 16) | (data[off + 3] << 24))


def slot_name(slot):
    if slot == SLOT_A:
        return "A"
    if slot == SLOT_B:
        return "B"
    if slot == SLOT_NONE:
        return "NONE"
    return "?(0x%02X)" % slot


def state_name(state):
    return STATE_TEXT.get(state, "?(%d)" % state)


def err_name(code):
    return ERR_TEXT.get(code, "?(%d)" % code)


def decode_ack_offset(data):
    return get_u32(data, 1)


def decode_nack_error(data):
    return data[5]


class Printer(object):
    """stdout/stderr helper that keeps the carriage-return progress line tidy."""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self._progress_open = False
        self._progress_len = 0
        self._progress_count = 0

    def _close_progress(self):
        if self._progress_open:
            sys.stdout.write("\n")
            self._progress_open = False
            self._progress_len = 0

    def message(self, text=""):
        self._close_progress()
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def debug(self, text):
        if self.verbose:
            self.message("  [dbg] " + text)

    def warn(self, text):
        self._close_progress()
        sys.stdout.write("WARNING: " + text + "\n")
        sys.stdout.flush()

    def error(self, text):
        self._close_progress()
        sys.stdout.flush()
        sys.stderr.write("ERROR: " + text + "\n")
        sys.stderr.flush()

    def progress(self, text, force=False):
        """Short carriage-return progress line; newline before any message."""
        self._progress_count += 1
        if not force and not sys.stdout.isatty() and (self._progress_count % 8):
            return
        pad = ""
        if self._progress_len > len(text):
            pad = " " * (self._progress_len - len(text))
        sys.stdout.write("\r" + text + pad)
        sys.stdout.flush()
        self._progress_open = True
        self._progress_len = len(text)


class Info(object):
    """Decoded INFO (0x83) reply."""

    def __init__(self, data):
        self.active = data[1]
        self.inactive = data[2]
        self.state = data[3]
        self.try_left = data[4]
        self.version = get_u16(data, 5)
        self.flags = data[7]

    @property
    def valid_a(self):
        return bool(self.flags & INFO_F_VALID_A)

    @property
    def valid_b(self):
        return bool(self.flags & INFO_F_VALID_B)

    @property
    def pending(self):
        return bool(self.flags & INFO_F_PENDING)

    def lines(self):
        return [
            "  active slot      : %s (state %s, try_left %d)"
            % (slot_name(self.active), state_name(self.state), self.try_left),
            "  inactive slot    : %s" % slot_name(self.inactive),
            "  running version  : 0x%04X" % self.version,
            "  device state     : %d (%s)" % (self.state, state_name(self.state)),
            "  pending update   : %s" % ("yes" if self.pending else "no"),
            "  slot A valid     : %s" % ("yes" if self.valid_a else "no"),
            "  slot B valid     : %s" % ("yes" if self.valid_b else "no"),
        ]


# ---------------------------------------------------------------------------
# CAN link
# ---------------------------------------------------------------------------

class CanLink(object):
    """Frames in/out for one node, with reply matching by id AND command byte."""

    def __init__(self, bus, node, printer, timeout):
        self.bus = bus
        self.node = node
        self.printer = printer
        self.timeout = timeout
        self.host_id = ID_HOST_BASE + node
        self.dev_id = ID_DEV_BASE + node
        self.other_ids = {}
        self.tx_frames = 0
        self.rx_frames = 0

    # -- raw io ------------------------------------------------------------
    def drain(self):
        """Throw away everything already queued (stale replies, old traffic)."""
        dropped = 0
        while True:
            try:
                msg = self.bus.recv(timeout=0.0)
            except Exception as exc:                       # backend hiccup
                raise ProtocolError("CAN receive failed while draining: %s" % exc)
            if msg is None:
                break
            dropped += 1
        if dropped:
            self.printer.debug("drained %d stale CAN frame(s)" % dropped)
        return dropped

    def send(self, cmd, payload=b""):
        data = bytearray(8)
        data[0] = cmd & 0xFF
        for i, byte in enumerate(payload[:DATA_BYTES_PER_FRAME]):
            data[1 + i] = byte
        try:
            msg = can.Message(arbitration_id=self.host_id,
                              is_extended_id=False,
                              data=bytes(data),
                              dlc=8)
        except TypeError:                                  # very old python-can
            msg = can.Message(arbitration_id=self.host_id,
                              extended=False,
                              data=bytes(data),
                              dlc=8)
        try:
            self.bus.send(msg)
        except Exception as exc:
            raise ProtocolError(
                "CAN transmit on id 0x%03X failed: %s (adapter up? bus terminated? "
                "another node acknowledging?)" % (self.host_id, exc))
        self.tx_frames += 1
        self.printer.debug("tx cmd 0x%02X %s" % (cmd, " ".join("%02X" % b for b in data)))

    def wait_reply(self, expected_cmds, timeout=None, drain_first=False):
        """Wait for a frame from our device whose command byte is expected.

        Frames for other arbitration ids or other command bytes are noted and
        skipped -- never mistaken for the reply we are waiting for.
        Returns the 8 byte payload (zero padded) or None on timeout.
        """
        if drain_first:
            self.drain()
        if timeout is None:
            timeout = self.timeout
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining < 0.0:
                return None
            try:
                msg = self.bus.recv(timeout=remaining)
            except Exception as exc:
                raise ProtocolError("CAN receive failed: %s" % exc)
            if msg is None:
                return None
            data = bytes(msg.data) if msg.data is not None else b""
            if msg.arbitration_id != self.dev_id:
                self.other_ids[msg.arbitration_id] = \
                    self.other_ids.get(msg.arbitration_id, 0) + 1
                self.printer.debug("ignoring frame id 0x%03X (not our device 0x%03X)"
                                   % (msg.arbitration_id, self.dev_id))
                continue
            if not data:
                self.printer.debug("ignoring empty frame from 0x%03X" % self.dev_id)
                continue
            if data[0] not in expected_cmds:
                self.printer.debug("ignoring device cmd 0x%02X (waiting for %s)"
                                   % (data[0], ", ".join("0x%02X" % c for c in expected_cmds)))
                continue
            self.rx_frames += 1
            return data.ljust(8, b"\x00")

    # -- helpers -----------------------------------------------------------
    def wrong_node_hint(self):
        """Look for a device answering on another node id 0x580 + n."""
        notes = []
        for arb_id in sorted(self.other_ids):
            if ID_DEV_BASE <= arb_id < ID_DEV_BASE + 128:
                notes.append("node %d (id 0x%03X) is answering on this bus, "
                             "try --node %d" % (arb_id - ID_DEV_BASE, arb_id,
                                                arb_id - ID_DEV_BASE))
        if not notes and self.other_ids:
            notes.append("bus traffic from %s was seen, but nothing from our "
                         "device id 0x%03X"
                         % (", ".join("0x%03X" % a for a in sorted(self.other_ids)),
                            self.dev_id))
        return (" Hint: " + "; ".join(notes) + ".") if notes else ""

    def no_reply_message(self, what):
        return ("%s: no reply from node %d (device id 0x%03X). Check that the CAN "
                "adapter is connected, the bus is terminated, the bitrate is %d, "
                "and that the device really is node %d.%s"
                % (what, self.node, self.dev_id, DEFAULT_BAUD, self.node,
                   self.wrong_node_hint()))

    def get_info(self, timeout=None, required=True, attempts=3):
        """Drain, send GET_INFO, return Info or None (or raise if required).

        Retried a few times on purpose: some adapters (measured on the ZQWL
        USBCANFD here) silently drop the first frame or two right after the
        channel is opened, so a single-shot GET_INFO looks like "no reply" even
        though the device is perfectly alive.  GET_INFO is idempotent, so
        resending is always safe.
        """
        self.drain()
        for attempt in range(max(1, attempts)):
            self.send(CMD_GET_INFO)
            # NOTE: never drain between retries.  A reply can take a few hundred
            # ms to come back through the adapter, and draining would throw away
            # a perfectly good (just late) answer -- measured: that alone made
            # 4 out of 6 requests look dead.
            data = self.wait_reply((RSP_INFO,), timeout)
            if data is not None:
                return Info(data)
            if attempt + 1 < attempts:
                self.printer.debug("GET_INFO attempt %d/%d got no reply, retrying"
                                   % (attempt + 1, attempts))
        if required:
            raise UsageError(self.no_reply_message("GET_INFO"))
        return None

    def send_wait_ack(self, cmd, payload=b"", what="command", timeout=None,
                      attempts=3):
        """Send a command and wait for its ACK, retrying a few lost frames.

        Safe for idempotent commands (START_UPDATE resets the receive state,
        which is exactly what a resend should do).
        """
        last = None
        self.drain()
        for attempt in range(max(1, attempts)):
            self.send(cmd, payload)
            data = self.wait_reply((RSP_ACK, RSP_NACK), timeout)
            if data is not None:
                if data[0] == RSP_NACK:
                    err = decode_nack_error(data)
                    off = decode_ack_offset(data)
                    raise ProtocolError(
                        "%s was rejected: NACK err=%d (%s), safe offset 0x%X"
                        % (what, err, err_name(err), off))
                return decode_ack_offset(data)
            last = attempt + 1
            if last < attempts:
                self.printer.debug("%s attempt %d/%d got no reply, retrying"
                                   % (what, last, attempts))
                time.sleep(0.05)
        raise ProtocolError(self.no_reply_message(what))

    def wait_ack(self, what, timeout=None):
        """Wait for ACK or NACK; return the ACK offset or raise ProtocolError."""
        data = self.wait_reply((RSP_ACK, RSP_NACK), timeout)
        if data is None:
            raise ProtocolError(self.no_reply_message(what))
        if data[0] == RSP_NACK:
            err = decode_nack_error(data)
            off = decode_ack_offset(data)
            raise ProtocolError("%s was rejected: NACK err=%d (%s), safe offset 0x%X"
                                % (what, err, err_name(err), off))
        return decode_ack_offset(data)


# ---------------------------------------------------------------------------
# Bus creation
# ---------------------------------------------------------------------------

def guess_interfaces(channel):
    """Best-effort list of python-can backends to try for this channel name."""
    low = channel.strip().lower()
    order = []
    if re.match(r"^v?can\d+$", low):
        order.append("socketcan")
    if low.startswith("com") or low.startswith("\\\\.\\com") or low.startswith("/dev/tty"):
        order.append("slcan")
    for prefix, name in (("pcan", "pcan"), ("vector", "vector"), ("kvaser", "kvaser"),
                         ("ixxat", "ixxat"), ("usb2can", "usb2can"),
                         ("systec", "systec"), ("canalyst", "canalystii"),
                         ("robotell", "robotell")):
        if low.startswith(prefix):
            order.append(name)
    for name in FALLBACK_INTERFACES:
        order.append(name)
    seen = set()
    unique = []
    for name in order:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def open_with_kwargs(kwargs):
    """can.Bus(**kwargs), with a fallback for other python-can layouts."""
    # ★gs_usb (candleLight/CANable) 需要先给 pyusb 装上 libusb 后端, 否则报
    #   NoBackendError: No backend available (本机没有系统级 libusb-1.0.dll)。
    #   usb_gs 是幂等的, 对 ZQWL/串口后端无影响。
    if str(kwargs.get("interface", "")).lower() == "gs_usb":
        try:
            import usb_gs          # noqa: F401
        except Exception:
            pass
    factory = getattr(can, "Bus", None)
    if factory is None:
        factory = can.interface.Bus
    return factory(**kwargs)


def bus_hint(channel, baud, tried, errors):
    return (
        "cannot open a CAN bus on channel '%s' at %d bit/s.\n"
        "Tried: %s\n"
        "%s\n"
        "Fix: install the adapter driver, then pass the backend explicitly with\n"
        "  --interface <name>   (or set the CAN_INTERFACE environment variable)\n"
        "Known python-can backends: slcan, pcan, vector, kvaser, ixxat, usb2can, "
        "socketcan, virtual.\n"
        "Examples:\n"
        "  --interface slcan    --channel COM3          (USB-serial adapter)\n"
        "  --interface pcan     --channel PCAN_USBBUS1  (Peak)\n"
        "  --interface vector   --channel 0             (Vector)\n"
        "  --interface kvaser   --channel 0             (Kvaser)\n"
        "  --interface ixxat    --channel 0             (Ixxat)\n"
        "  --interface usb2can  --channel 0             (8devices)\n"
        "  --interface socketcan --channel can0         (Linux SocketCAN)\n"
        "  --interface virtual  --channel test          (python-can loopback, no "
        "hardware)\n"
        "Install python-can itself with: make host-deps"
        % (channel, baud, ", ".join(tried),
           "\n".join(errors) if errors else "  (no backend reported a reason)"))


def open_bus(channel, baud, interface, printer):
    """Open the CAN bus.

    With --interface the backend is handed to python-can as interface=... .
    Without it no interface is passed at all, so python-can picks its own
    default.  Only if that default fails are the backends that fit the channel
    name probed as well, so a bare "--channel can0" still works on Linux.

    --interface zqwl (or "auto") uses the vendor serial protocol of a
    ZQWL USBCANFD adapter through scripts/zqwl.py: those adapters are NOT
    slcan, and python-can cannot drive them.
    """
    if interface in ("zqwl",) or interface == "auto":
        try:
            from zqwl import ZqwlBus, find_zqwl_port, ZqwlError
        except Exception as exc:
            raise UsageError("zqwl transport unavailable: %s" % exc)
        port = channel if str(channel).upper().startswith("COM") else find_zqwl_port()
        if not port:
            if interface == "auto":
                printer.debug("no ZQWL adapter found, falling back to python-can")
            else:
                raise UsageError("no ZQWL adapter found (VID 0x3562); "
                                 "pass --channel COMx")
        else:
            try:
                bus = ZqwlBus(port, bitrate=baud)
            except ZqwlError as exc:
                if interface == "auto":
                    printer.debug("zqwl on %s failed (%s), falling back to python-can"
                                  % (port, exc))
                else:
                    raise UsageError(str(exc))
            else:
                printer.message("CAN: ZQWL adapter on %s, %d bit/s (vendor serial "
                                "protocol, %s)" % (port, baud,
                                                   bus.last_heartbeat.text()
                                                   if bus.last_heartbeat else "no heartbeat"))
                return bus

    attempts = [interface if interface else None]
    if not interface:
        attempts.extend(guess_interfaces(channel))
    tried = []
    errors = []
    for name in attempts:
        label = name if name else "(python-can default)"
        tried.append(label)
        kwargs = {"channel": channel, "bitrate": baud}
        if name:
            kwargs["interface"] = name
        try:
            bus = open_with_kwargs(kwargs)
        except Exception as exc:
            errors.append("  %s: %s" % (label, exc))
            printer.debug("interface %s unavailable: %s" % (label, exc))
            continue
        backend = type(bus).__name__
        printer.message("CAN: interface %s, channel '%s', %d bit/s (backend %s)"
                        % (label, channel, baud, backend))
        if name is None and "virtual" in backend.lower():
            printer.warn("python-can opened its virtual loopback backend, so no real "
                         "adapter is in use. Pass --interface with a real backend "
                         "(slcan, pcan, vector, kvaser, ixxat, usb2can, socketcan).")
        return bus
    raise UsageError(bus_hint(channel, baud, tried, errors))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def print_info(printer, info, header="Device info"):
    printer.message(header)
    for line in info.lines():
        printer.message(line)


def handshake_timeout(args):
    """Per-attempt reply timeout for the GET_INFO that opens a command.

    Kept separate from the per-frame timeout used during a transfer: streaming
    wants short timeouts so a real stall is noticed quickly.
    """
    t = getattr(args, "info_timeout", None)
    if t is None:
        return args.timeout
    return max(t, args.timeout)


def handshake_attempts(args):
    """How many GET_INFO frames to send before giving up on reaching the device.

    A long reply timeout alone does NOT work.  Measured on this bench with a
    device stuck in a restart loop (CAN controller alive only ~1.2 s out of
    every ~7.5 s): a single GET_INFO sent during the dark part of the cycle is
    never answered, however long the host waits -- the request is simply gone.
    Only a request that arrives while the device is actually listening gets an
    answer.  So the handshake must keep RE-SENDING across a whole cycle; N
    attempts at --info-timeout seconds each must comfortably exceed that cycle.
    GET_INFO is idempotent, so re-sending is always safe.
    """
    t = handshake_timeout(args)
    if t <= 1.5:
        return 30
    return max(6, int(30.0 / t) + 6)


def cmd_info(link, printer, args):
    info = link.get_info(timeout=handshake_timeout(args), attempts=handshake_attempts(args))
    print_info(printer, info,
               "Device info (node %d, host id 0x%03X, device id 0x%03X)"
               % (link.node, link.host_id, link.dev_id))
    if info.state in (STATE_RECEIVING, STATE_VERIFYING):
        printer.warn("the device is in the middle of a transfer (state %d, %s); "
                     "run 'abort' before starting a new update"
                     % (info.state, state_name(info.state)))
    if info.pending:
        printer.warn("an update is pending confirmation: the bootloader will keep "
                     "trying the pending slot until try_left (%d) reaches 0, then "
                     "roll back" % info.try_left)
    return 0


def read_image(path):
    if not path:
        raise UsageError("update requires --bin <file>")
    if not os.path.isfile(path):
        raise UsageError("image file not found: %s" % path)
    try:
        with open(path, "rb") as handle:
            image = handle.read()
    except OSError as exc:
        raise UsageError("cannot read image file %s: %s" % (path, exc))
    size = len(image)
    if size == 0:
        raise UsageError("image file is empty: %s" % path)
    if size < IMAGE_MIN:
        raise UsageError("image is %d bytes; the device refuses images below "
                         "%d bytes (%s)" % (size, IMAGE_MIN, path))
    if size > SLOT_SIZE:
        raise UsageError("image is %d bytes; one slot holds at most %d bytes "
                         "(%s)" % (size, SLOT_SIZE, path))
    return image


def choose_slot(info, requested, printer):
    """Pick the target slot; refuses to overwrite the slot that is running."""
    if requested == "auto":
        target = info.inactive
        if target not in (SLOT_A, SLOT_B):
            raise ProtocolError("the device reported an invalid inactive slot "
                                "(0x%02X); run 'info' again or reset the device"
                                % target)
        printer.message("Target slot: %s (the slot the device reported as inactive)"
                        % slot_name(target))
        return target
    wanted = SLOT_A if requested == "a" else SLOT_B
    if wanted != info.inactive:
        raise UsageError(
            "--slot %s was requested but the device reports slot %s as inactive "
            "(it is running slot %s). Refusing to overwrite the running image: "
            "use --slot auto, or --slot %s."
            % (requested, slot_name(info.inactive), slot_name(info.active),
               slot_name(info.inactive).lower()))
    printer.message("Target slot: %s (matches the reported inactive slot)"
                    % slot_name(wanted))
    return wanted


def sync_block(link, printer, frames, block_end, sync_timeout):
    """Send one SYNC and interpret the reply.

    Returns ("ok", offset), ("nack", offset, error) or ("lost",).
    A lost SYNC is retried up to SYNC_RETRY_MAX times WITHOUT resending data.
    The device erases and programs flash while handling SYNC, so the timeout
    here is the dedicated long one, not the short command timeout.
    """
    payload = u16le(frames) + u24le(block_end)
    link.drain()   # drop any stale reply before starting a new block handshake
    attempts = 0
    while True:
        link.send(CMD_SYNC, payload)
        data = link.wait_reply((RSP_ACK, RSP_NACK), sync_timeout)
        if data is not None:
            break
        attempts += 1
        if attempts > SYNC_RETRY_MAX:
            printer.warn("no reply to SYNC after %d attempts (%d frames, block end "
                         "0x%X)" % (attempts, frames, block_end))
            return ("lost",)
        printer.debug("SYNC timeout, resending the same SYNC (retry %d/%d)"
                      % (attempts, SYNC_RETRY_MAX))
    if data[0] == RSP_ACK:
        return ("ok", decode_ack_offset(data))
    return ("nack", decode_ack_offset(data), decode_nack_error(data))


def stream_image(link, printer, image, size, target, args):
    """Send START_UPDATE, all DATA blocks, and END.  Returns (crc, end_offset)."""
    crc = zlib.crc32(image) & 0xFFFFFFFF

    pos = link.send_wait_ack(CMD_START_UPDATE,
                             bytes((target,)) + u24le(size) +
                             u16le(args.version) + b"\x00",
                             what="START_UPDATE",
                             timeout=max(1.0, args.timeout * 5.0))
    if pos != 0:
        printer.warn("the device confirmed offset 0x%X at START_UPDATE "
                     "(expected 0); resuming from there" % pos)

    best = pos                  # highest offset the device ever confirmed
    retransmits = 0             # total recoveries, for display only
    stall = 0                   # retransmits without forward progress
    frames_sent = 0
    page_pause = max(0.0, float(getattr(args, "page_pause_ms", 0.0))) / 1000.0
    sync_timeout = max(args.timeout, SYNC_TIMEOUT_DEFAULT)

    if args.verbose:
        printer.message("Streaming %d bytes in blocks of at most %d frames "
                        "(%d bytes), %d bit/s, SYNC timeout %.2f s"
                        % (size, FRAMES_PER_BLOCK,
                           FRAMES_PER_BLOCK * DATA_BYTES_PER_FRAME, args.baud,
                           sync_timeout))
    printer.progress("  0.0%%  0/%d B  retx 0" % size, force=True)

    while pos < size:
        block_start = pos
        block_limit = min(size, block_start + FRAMES_PER_BLOCK * DATA_BYTES_PER_FRAME)
        frames = 0
        while pos < block_limit:
            chunk = image[pos:pos + DATA_BYTES_PER_FRAME]
            padded = chunk + bytes((IMAGE_PAD_BYTE,)) * (DATA_BYTES_PER_FRAME - len(chunk))
            link.send(CMD_DATA, padded)
            pos += len(chunk)
            frames += 1
            frames_sent += 1
            # Only needed for firmware that flushes a page in the middle of a
            # block (the current device flushes on SYNC only); off by default.
            if (page_pause > 0.0 and pos < size and pos > block_start
                    and (pos % FLASH_PAGE_SIZE) == 0):
                time.sleep(page_pause)
        block_end = pos

        outcome = sync_block(link, printer, frames, block_end, sync_timeout)
        if outcome[0] == "ok":
            offset = outcome[1]
        elif outcome[0] == "nack":
            offset = outcome[1]
            err = outcome[2]
            if err in FATAL_ERRORS:
                raise ProtocolError(
                    "the device rejected block ending at 0x%X: NACK err=%d (%s). "
                    "Retrying cannot help; the transfer was stopped at offset 0x%X."
                    % (block_end, err, err_name(err), offset))
            retransmits += 1
            printer.message("NACK err=%d (%s) at 0x%X; retransmitting from the safe "
                            "offset 0x%X (retransmit %d)"
                            % (err, err_name(err), block_end, offset, retransmits))
        else:
            retransmits += 1
            offset = best
            printer.message("SYNC lost; falling back to the last confirmed offset "
                            "0x%X (retransmit %d)" % (offset, retransmits))

        if offset > pos:
            raise ProtocolError("the device asked to resume at 0x%X, beyond the "
                                "0x%X bytes already sent; protocol desynchronised"
                                % (offset, pos))

        if offset > best:
            best = offset
            stall = 0
        else:
            stall += 1
        if stall > args.retries:
            raise ProtocolError(
                "stopping: %d retransmits without forward progress (still stuck at "
                "offset 0x%X of %d bytes). The device cannot accept this block. "
                "Check the wiring/bitrate, try 'abort' and 'info', or raise "
                "--retries if the link is genuinely noisy." % (stall, offset, size))
        pos = offset
        printer.progress("  %5.1f%%  %d/%d B  retx %d"
                         % (100.0 * pos / size, pos, size, retransmits))

    printer.progress("  100.0%%  %d/%d B  retx %d" % (size, size, retransmits),
                     force=True)
    printer.message("Sent %d bytes in %d DATA frames (%d retransmit(s))."
                    % (size, frames_sent, retransmits))
    printer.message("Image CRC32: 0x%08X -- asking the device to verify."
                    % crc)

    # Let any late SYNC reply land first: the wire protocol has no transaction
    # id, so a delayed NACK from the last block would otherwise be mistaken for
    # the answer to END (measured on the ZQWL adapter).
    time.sleep(0.15)
    link.drain()
    link.send(CMD_END, u32le(crc))
    data = link.wait_reply((RSP_ACK, RSP_NACK), max(2.0, args.timeout * 10.0))
    for _ in range(3):
        if data is None or data[0] != RSP_NACK:
            break
        err = decode_nack_error(data)
        if err == ERR_FRAMES:
            printer.warn("END: got a late SYNC reply (err=7); asking again")
            time.sleep(0.15)
            link.drain()
            link.send(CMD_END, u32le(crc))
            data = link.wait_reply((RSP_ACK, RSP_NACK), max(2.0, args.timeout * 10.0))
            continue
        if err == ERR_STATE:
            # the device already finished verifying: our previous END arrived and
            # only its ACK was lost.  Treat it as verified and move on to COMMIT.
            printer.warn("END: device says it already finished verifying (err=3)")
            data = bytes((RSP_ACK,)) + bytes(data[1:])
        break
    if data is None:
        raise ProtocolError("no reply to END after the device was asked to CRC the "
                            "whole slot; it may still be verifying or the link "
                            "dropped. Nothing was committed.")
    if data[0] == RSP_NACK:
        err = decode_nack_error(data)
        raise ProtocolError(
            "END was rejected: NACK err=%d (%s) at offset 0x%X. The image on the "
            "device is NOT valid, so it was not committed."
            % (err, err_name(err), decode_ack_offset(data)))
    confirmed = decode_ack_offset(data)
    if confirmed != size:
        printer.warn("END ACK reported %d bytes confirmed, expected %d"
                     % (confirmed, size))
    printer.message("The device verified %d bytes, CRC32 matches." % confirmed)
    return crc


def update_confirmed(info, slot, version):
    """Only the requested running, formally confirmed application is success."""
    return (info is not None and info.active == slot and info.version == version
            and info.state == STATE_APP and not info.pending and info.try_left == 0)


def poll_info_until(link, printer, seconds, initial_delay, wanted_slot=None,
                    wanted_version=None):
    """Poll INFO until slot matches, or the requested application is confirmed."""
    if initial_delay > 0.0:
        time.sleep(initial_delay)
    deadline = time.monotonic() + seconds
    last = None
    while True:
        info = link.get_info(required=False)
        if info is not None:
            last = info
            if wanted_version is not None:
                if update_confirmed(info, wanted_slot, wanted_version):
                    return info
            elif wanted_slot is None or info.active == wanted_slot:
                return info
        if time.monotonic() >= deadline:
            return last
        time.sleep(0.25)


def pick_bin(args, target_slot, printer):
    """Resolve which image file to send.

    --bin<file> wins if given.  Otherwise --bin-a/--bin-b let the caller hand
    over both builds and this function picks the one matching the slot the
    device reported as inactive -- which is what makes one-key OTA possible
    without knowing which slot is running.
    """
    if args.bin:
        return args.bin
    path = args.bin_a if target_slot == SLOT_A else args.bin_b
    if not path:
        raise UsageError(
            "no image for slot %s: pass --bin <file>, or both --bin-a and "
            "--bin-b so the tool can pick automatically" % slot_name(target_slot))
    printer.message("Image for slot %s: %s" % (slot_name(target_slot), path))
    return path


def cmd_foc_stat(link, printer, args):
    """Read the application's own FOC loop counter.

    This is the proof that the OTA'd application code actually runs (not just
    that the device answers): the counter must grow between two calls, and
    init_rc is what foc_init() returned before ota_confirm().
    """
    link.drain()
    for attempt in range(3):
        link.send(CMD_FOC_STAT)
        data = link.wait_reply((RSP_FOC,), args.timeout)
        if data is not None:
            loops = decode_ack_offset(data)
            rc = data[5]
            if rc > 127:
                rc -= 256
            printer.message("FOC: foc_loop count = %d   foc_init returned %d"
                            % (loops, rc))
            return 0
    raise ProtocolError(link.no_reply_message("GET_FOC_STAT"))


def check_version_guard(printer, args, info):
    """Refuse to flash a version the device already runs (or an older one).

    Why this exists
    ---------------
    Once an image is on the device, the version is the ONLY thing that tells two
    builds apart.  A stale or defaulted version silently re-labels a brand new
    image as an old release (measured 2026-09: a fresh build went out as 0x0100
    on top of 0x0808, and only the flashing log showed it).  After that "which
    firmware is running?" is unanswerable and the metadata version means nothing.

    The check runs right after GET_INFO and BEFORE the first write, so a refusal
    costs nothing and leaves the device untouched.  It only compares against the
    ACTIVE slot (the protocol reports no version for the inactive one), which is
    exactly the number a human uses to answer "what is running?".
    """
    if args.force_version:
        printer.warn("--force-version: flashing version 0x%04X although the device "
                     "already runs 0x%04X in slot %s"
                     % (args.version, info.version, slot_name(info.active)))
        return
    if args.version > info.version:
        return
    raise UsageError(
        "refusing to flash version 0x%04X: the device already runs 0x%04X in "
        "slot %s.\n"
        "  A build must carry a version STRICTLY NEWER than the running one -- "
        "otherwise two different firmwares end up with the same number and "
        "nothing can tell them apart afterwards.\n"
        "  Fix: run 'make version-bump' (or just 'make ota-onekey', which bumps "
        "the version automatically).\n"
        "  If you really mean to re-label the image, or to put the same version "
        "into the other slot, add --force-version."
        % (args.version, info.version, slot_name(info.active)))


def cmd_update(link, printer, args):
    if args.version < 0 or args.version > 0xFFFF:
        raise UsageError("--version must fit in 16 bits (0x0000..0xFFFF)")

    # (a) ask the device which slot is running before touching anything
    link.drain()
    info = link.get_info(timeout=handshake_timeout(args),
                         attempts=handshake_attempts(args))
    print_info(printer, info, "Device before update:")
    if info.state in (STATE_RECEIVING, STATE_VERIFYING):
        raise ProtocolError(
            "the device is busy with another transfer (state %d, %s). Wait for it "
            "to finish, or run 'abort' first."
            % (info.state, state_name(info.state)))

    # (a2) Version guard: refuse to flash a version the device already runs (or an
    #      older one).  It must run before the FIRST flash write, so it sits here,
    #      before the image is even read.
    check_version_guard(printer, args, info)

    target = choose_slot(info, args.slot, printer)

    # (b) load and check the image
    image = read_image(pick_bin(args, target, printer))
    size = len(image)
    crc = zlib.crc32(image) & 0xFFFFFFFF
    printer.message("Image      : %s" % args.bin)
    printer.message("Size       : %d bytes (%.1f KiB)" % (size, size / 1024.0))
    printer.message("CRC32      : 0x%08X" % crc)
    printer.message("Version    : 0x%04X" % args.version)

    if args.dry_run:
        printer.message("--dry-run: all pre-flight checks passed (device state, "
                        "version guard, image load). Nothing was written; would "
                        "have flashed 0x%04X into slot %s."
                        % (args.version, slot_name(target)))
        return 0

    # (c..f) transfer and verify
    try:
        stream_image(link, printer, image, size, target, args)
    except HostError:
        try:
            link.drain()
            link.send(CMD_ABORT)
            abort_reply = link.wait_reply((RSP_ACK, RSP_NACK), max(1.0, args.timeout * 5.0))
            if abort_reply is None:
                printer.debug("best-effort ABORT got no reply")
            elif abort_reply[0] == RSP_NACK:
                printer.debug("best-effort ABORT was NACKed (err=%d)"
                              % decode_nack_error(abort_reply))
            else:
                printer.message("Sent ABORT: the half written image was marked "
                                "invalid on the device.")
        except HostError as exc:
            printer.debug("best-effort ABORT failed: %s" % exc.message)
        raise

    if args.no_commit:
        printer.message("--no-commit: metadata was NOT written and the device was "
                        "NOT rebooted. The image is written and CRC-verified in "
                        "slot %s, but the device keeps booting slot %s."
                        % (slot_name(target), slot_name(info.active)))
        return 0

    # (g) commit: metadata write + reboot
    # COMMIT is idempotent (writing the metadata twice is harmless), so retry it:
    # a late reply to an earlier command can be mistaken for COMMIT's answer when
    # the adapter delivers a duplicate/late frame.
    link.drain()
    link.send(CMD_COMMIT)
    data = link.wait_reply((RSP_ACK, RSP_NACK), max(2.0, args.timeout * 10.0))
    for attempt in range(3):
        if data is None or data[0] == RSP_ACK:
            break
        err = decode_nack_error(data)
        printer.warn("COMMIT attempt %d got NACK err=%d (%s); retrying"
                     % (attempt + 1, err, err_name(err)))
        time.sleep(0.15)
        link.drain()
        link.send(CMD_COMMIT)
        data = link.wait_reply((RSP_ACK, RSP_NACK), max(2.0, args.timeout * 10.0))
    if data is not None and data[0] == RSP_NACK:
        # Do NOT fail here.  COMMIT is the one command that deliberately reboots
        # the device, so a NACK can be a reply to a *duplicate* COMMIT that
        # arrived before the reset finished (measured: the update really did
        # succeed while the tool reported failure).  Fall through to the state
        # polling below and let the device itself decide.
        err = decode_nack_error(data)
        printer.warn("COMMIT answered NACK err=%d (%s); checking the device state "
                     "instead of failing (the update may well have succeeded)"
                     % (err, err_name(err)))
    if data is None:
        printer.warn("no ACK for COMMIT; the device may have rebooted before the "
                     "ACK reached the host. Checking the device now.")
    printer.message("COMMIT accepted; the device writes metadata and reboots into "
                    "the new image.")

    after = poll_info_until(link, printer, 6.0, 2.0, wanted_slot=target,
                            wanted_version=args.version)
    if after is None:
        raise ProtocolError("the device did not answer GET_INFO after COMMIT. It "
                            "may be stuck in the bootloader with a slot that does "
                            "not run; check the image with 'make check' and "
                            "power-cycle the board.")
    print_info(printer, after, "Device after commit/reboot:")
    if update_confirmed(after, target, args.version):
        printer.message("Update OK: active slot is now %s (running version 0x%04X). "
                        "The device records the version the application itself "
                        "compiled in (make OTA_VERSION=...)."
                        % (slot_name(after.active), after.version))
        return 0
    raise ProtocolError(
        "update not confirmed: expected slot %s, version 0x%04X, APP, "
        "pending=no, try_left=0; got slot %s, version 0x%04X, state %s, "
        "pending=%s, try_left=%d. Check the application and run 'info' again."
        % (slot_name(target), args.version, slot_name(after.active), after.version,
           state_name(after.state), "yes" if after.pending else "no", after.try_left))


def cmd_rollback(link, printer, args):
    before = link.get_info(required=False)
    if before is not None:
        print_info(printer, before, "Device before rollback:")
    else:
        printer.warn("no INFO before rollback; continuing anyway")
    link.drain()
    link.send(CMD_ROLLBACK)
    data = link.wait_reply((RSP_ACK, RSP_NACK), max(2.0, args.timeout * 10.0))
    if data is None:
        raise ProtocolError(link.no_reply_message("ROLLBACK"))
    if data[0] == RSP_NACK:
        err = decode_nack_error(data)
        raise ProtocolError("ROLLBACK was rejected: NACK err=%d (%s). The other slot "
                            "is probably not valid." % (err, err_name(err)))
    printer.message("ROLLBACK accepted; waiting for the device to reboot.")
    after = poll_info_until(link, printer, 8.0, 2.0)
    if after is None:
        raise ProtocolError("the device did not answer GET_INFO after rollback; "
                            "check the board and the wiring")
    print_info(printer, after, "Device after rollback:")
    if before is not None and after.active == before.active:
        raise ProtocolError("the active slot is still %s after rollback; the "
                            "rollback did not take effect"
                            % slot_name(after.active))
    printer.message("Rollback OK: active slot is now %s."
                    % slot_name(after.active))
    return 0


def cmd_enter_boot(link, printer, args):
    """ENTER_BOOT on the app: ACK then reset; the bootloader only listens for a
    500 ms window after reset, so keep sending for up to 3 s and confirm with
    GET_INFO that state == 0."""
    link.drain()
    link.send(CMD_ENTER_BOOT)
    reply = link.wait_reply((RSP_ACK, RSP_NACK), max(1.0, args.timeout * 5.0))
    if reply is not None and reply[0] == RSP_NACK:
        printer.debug("ENTER_BOOT was NACKed (err=%d); still retrying"
                      % decode_nack_error(reply))
    printer.message("ENTER_BOOT sent; spamming it every 50 ms for up to 3 s while "
                    "the device reboots into the bootloader.")

    deadline = time.monotonic() + 3.0
    last_state = None
    next_info = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_info:
            info = link.get_info(required=False)
            if info is not None:
                last_state = info.state
                if info.state == STATE_BOOT:
                    print_info(printer, info, "Device now in the bootloader:")
                    printer.message("enter-boot OK: the device is in the bootloader "
                                    "(state 0), ready for 'update'.")
                    return 0
            next_info = now + 0.25
        else:
            link.send(CMD_ENTER_BOOT)
            link.wait_reply((RSP_ACK, RSP_NACK), 0.05)
        time.sleep(0.05)

    info = link.get_info(required=False)
    if info is not None and info.state == STATE_BOOT:
        print_info(printer, info, "Device now in the bootloader:")
        printer.message("enter-boot OK: the device is in the bootloader (state 0).")
        return 0
    state = info.state if info is not None else last_state
    raise ProtocolError(
        "the device did not enter the bootloader within 3 s (last known state: %s). "
        "If it is still running the application and the application does not handle "
        "ENTER_BOOT, hold the reset button and retry, or power-cycle the board and "
        "run 'enter-boot' again."
        % (state_name(state) if state is not None else "no reply at all"))


def cmd_reset(link, printer, args):
    link.drain()
    link.send(CMD_RESET)
    link.wait_ack("RESET", max(2.0, args.timeout * 10.0))
    printer.message("RESET accepted: the device is rebooting.")
    return 0


def cmd_abort(link, printer, args):
    link.drain()
    link.send(CMD_ABORT)
    link.wait_ack("ABORT", max(2.0, args.timeout * 10.0))
    printer.message("ABORT accepted: the device left receiving mode.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def int_auto(text):
    """Accept 500000, 0x7A120 and plain decimal forms."""
    try:
        return int(text, 0)
    except ValueError:
        return int(text, 10)


VALUE_OPTIONS = (
    "--channel", "--baud", "--interface", "-i", "--node", "--timeout",
    "--bin", "--version", "--slot", "--retries", "--page-pause-ms",
)

SUBCOMMANDS = ("info", "update", "rollback", "enter-boot", "reset", "abort")


def normalize_argv(argv):
    """Drop value-taking options whose value is missing.

    The Makefile expands an empty variable into nothing, which would turn
    '--channel --baud 500000' into channel='--baud' and
    '--interface info' into interface='info' with no subcommand left. Removing
    such an option lets the environment fallback (CAN_CHANNEL / CAN_BAUD /
    CAN_INTERFACE) apply instead.  A token that is one of the subcommand names
    can never be a real channel or backend name, so it is treated as a missing
    value too.
    """
    out = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in VALUE_OPTIONS:
            following = argv[index + 1] if index + 1 < len(argv) else None
            if following is None:
                index += 1                     # option at the end: drop it
                continue
            if following == "":
                index += 2                     # drop the option AND its empty value
                continue
            if following.startswith("-") or following in SUBCOMMANDS:
                index += 1                     # value missing: drop just the option
                continue
            out.append(token)
            out.append(following)
            index += 2
            continue
        out.append(token)
        index += 1
    return out


EPILOG = """\
commands:
  info         ask the device for its active/inactive slot, state and flags
  update       write an image into the inactive slot and (unless --no-commit)
               commit it: metadata is written and the device reboots
  rollback     switch back to the other slot and reboot
  enter-boot   make the running application reset into the bootloader
  reset        soft reset the device
  abort        leave receiving mode and mark a half written image invalid

examples:
  %(prog)s --channel can0 --baud 500000 --node 0 info
  %(prog)s --channel can0 --baud 500000 --node 0 update --bin build/app_slotB.bin
  %(prog)s --channel can0 --baud 500000 --node 0 update --bin app.bin --version 0x0101
  %(prog)s --channel can0 --baud 500000 --node 0 update --bin app.bin --no-commit
  %(prog)s --channel can0 --baud 500000 --node 0 rollback
  %(prog)s --channel can0 --baud 500000 --node 0 enter-boot

exit codes: 0 success, 1 protocol/device failure, 2 usage/environment failure.
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ota_host.py",
        description="PC-side host for the N32G455REL7 CAN-OTA protocol "
                    "(CAN 2.0A, host id 0x600+node, device id 0x580+node).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", default=None,
                        help="CAN channel/adapter name, e.g. can0, PCAN_USBBUS1, COM3 "
                             "(falls back to $CAN_CHANNEL)")
    parser.add_argument("--baud", type=int_auto, default=None,
                        help="CAN bitrate in bit/s (falls back to $CAN_BAUD, "
                             "default %d)" % DEFAULT_BAUD)
    parser.add_argument("--interface", "-i", default=None,
                        help="python-can backend: slcan, pcan, vector, kvaser, "
                             "ixxat, usb2can, socketcan, virtual, ... (falls back to "
                             "$CAN_INTERFACE; when omitted, python-can picks its own "
                             "default)")
    parser.add_argument("--node", type=int_auto, default=0,
                        help="device node number 0..127 (default 0)")
    parser.add_argument("--timeout", type=float, default=1.5,
                        help="reply timeout in seconds (default 0.2)")
    parser.add_argument("--info-timeout", type=float, default=None,
                        help="reply timeout (s) for the GET_INFO handshake that "
                             "starts a command, default = --timeout. Raise it when "
                             "the device only answers during a short window of a "
                             "restart loop: one attempt then spans the whole cycle "
                             "(measured on this bench: alive ~1.2 s every ~7.5 s, so "
                             "--info-timeout 10 finds it on the first attempt)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print every frame and every internal step")

    subparsers = parser.add_subparsers(dest="command", metavar="command")
    subparsers.required = True

    subparsers.add_parser("info", help="read slot/state/flag information")

    update = subparsers.add_parser("update", help="write and commit a new image")
    update.add_argument("--bin", default=None, help="raw firmware image (.bin)")
    update.add_argument("--bin-a", default=None,
                        help="image for slot A; used with --bin-b so the tool can "
                             "pick the file matching the device's inactive slot "
                             "(this is what one-key OTA uses)")
    update.add_argument("--bin-b", default=None, help="image for slot B")
    update.add_argument("--version", type=int_auto, required=True,
                        help="16-bit version to record (REQUIRED: it must match "
                             "the version compiled into the image; the Makefile "
                             "passes it from ota_version.txt via "
                             "scripts/version.py)")
    update.add_argument("--force-version", action="store_true",
                        help="allow a version that is NOT newer than what the "
                             "device already runs (re-label / flash the same "
                             "version into the other slot)")
    update.add_argument("--dry-run", action="store_true",
                        help="run every pre-flight check (device state, version "
                             "guard, image load) and stop before the first write")
    update.add_argument("--slot", choices=("auto", "a", "b"), default="auto",
                        help="target slot (default auto = the inactive slot)")
    update.add_argument("--no-commit", action="store_true",
                        help="write and verify only: no metadata write, no reboot")
    update.add_argument("--retries", type=int, default=5,
                        help="max retransmits without forward progress before "
                             "giving up (default 5)")
    update.add_argument("--page-pause-ms", type=float, default=0.0,
                        help="host-side pause after the frame that crosses a flash "
                             "page boundary; only useful for firmware that erases "
                             "mid-block (default 0 = off)")

    subparsers.add_parser("foc-stat", help="read the app FOC loop counter")
    subparsers.add_parser("rollback", help="roll back to the other slot")
    subparsers.add_parser("enter-boot", help="reset the app into the bootloader")
    subparsers.add_parser("reset", help="soft reset the device")
    subparsers.add_parser("abort", help="abort an in-progress transfer")
    return parser


def resolve_bus_settings(args):
    channel = (args.channel or os.environ.get("CAN_CHANNEL") or "").strip()
    if not channel:
        raise UsageError("no CAN channel: pass --channel <name> or set the "
                         "CAN_CHANNEL environment variable "
                         "(for example --channel can0, or PCAN_USBBUS1 / COM3 on "
                         "Windows)")

    if args.baud is None:
        env_baud = (os.environ.get("CAN_BAUD") or "").strip()
        baud = int_auto(env_baud) if env_baud else DEFAULT_BAUD
    else:
        baud = args.baud
    if baud <= 0:
        raise UsageError("--baud must be a positive bitrate in bit/s")

    if args.node < 0 or args.node > CAN_NODE_MAX:
        raise UsageError("--node must be 0..%d (0x600 + node must stay a valid "
                         "11-bit identifier)" % CAN_NODE_MAX)

    if args.timeout <= 0.0:
        raise UsageError("--timeout must be greater than 0 seconds")

    if getattr(args, "retries", 0) < 0:
        raise UsageError("--retries must be 0 or more")

    # --interface falls back to CAN_INTERFACE; an empty value means "let
    # python-can pick its own default".
    interface = (args.interface or os.environ.get("CAN_INTERFACE") or "").strip()
    if not interface:
        interface = None

    return channel, baud, interface


HANDLERS = {
    "info": cmd_info,
    "foc-stat": cmd_foc_stat,
    "update": cmd_update,
    "rollback": cmd_rollback,
    "enter-boot": cmd_enter_boot,
    "reset": cmd_reset,
    "abort": cmd_abort,
}


def main(argv=None):
    global VERBOSE

    raw = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(normalize_argv(raw))

    VERBOSE = bool(args.verbose)
    printer = Printer(args.verbose)

    bus = None
    try:
        channel, baud, interface = resolve_bus_settings(args)
        if can is None:
            raise UsageError(
                "python-can is not installed (%s).\n"
                "Install it with:  make host-deps\n"
                "or:               <python> -m pip install python-can"
                % CAN_IMPORT_ERROR)
        bus = open_bus(channel, baud, interface, printer)
        args.baud = baud
        link = CanLink(bus, args.node, printer, args.timeout)
        printer.debug("host id 0x%03X -> device id 0x%03X, timeout %.3f s"
                      % (link.host_id, link.dev_id, args.timeout))
        result = HANDLERS[args.command](link, printer, args)
        return 0 if result is None else result
    except HostError as exc:
        printer.error(exc.message)
        return exc.exit_code
    except KeyboardInterrupt:
        printer.error("interrupted by user")
        return 1
    finally:
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
