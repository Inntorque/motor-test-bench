#!/usr/bin/env python3
"""
zqwl.py -- transport for ZQWL USBCANFD adapters (vendor serial protocol)

Why this exists
---------------
The ZQWL-CANFD adapter on this bench is NOT a LAWICEL/slcan device: python-can's
slcan backend "opens" it but no frame ever moves. The vendor documents their own
protocol instead (ZQWL-USBCANFD second-development protocol V1.05, which matches
what a raw probe sees: no reply to slcan commands).

This module speaks that protocol and exposes a python-can compatible bus, so
the whole CAN-OTA toolchain (scripts/ota_host.py, make ota-onekey, F9) works
with the hardware that is actually on the desk.

Protocol summary (from the vendor manual, all frames are raw hex over a CDC
virtual COM port, N,8,1, baud is auto-matched by the hardware):

  Parameter config (22 bytes):
      49 3B <func> <52=read|57=write> <16 data bytes> 45 2E
      func 0x42 = set CAN params: data[0]=channel, data[1]=custom-baud flag,
                  data[2]=baud code (high nibble = arbitration rate,
                  low nibble = data rate; 2 = 500 kbit/s for CAN)
      func 0x44 = system control: data[0]=apply(1), data[1]=reset,
                  data[2]=CAN0 enable, data[3]=CAN1 enable

  CAN frame (host -> device, and device -> host):
      5A <BYTE1> <BYTE2> <ID[4]> <data...> A5
      BYTE1: bit7 = channel bit2, bits[3:0] = DLC
      BYTE2: bits[7:6] = send type, bits[4:3] = channel bits,
             bit2 = 1 extended id, bit1 = 1 remote frame, bit0 = BRS
      ID:    standard uses bytes 5..6 (low 11 bits)

  Heartbeat (device -> host, 17 bytes, BYTE1 = 0xFF): device/tx/rx status,
      per-channel bus state (00 normal, 01 warning, 10 error passive,
      11 bus off) and the CAN error counters.  Sent every second, and
      immediately after every frame the host sends.

Usage as a tool (bus check, no project code needed):
    <python> scripts/zqwl.py                          # find and report adapters
    <python> scripts/zqwl.py -c COM4 --listen 3       # configure 500k and listen
    <python> scripts/zqwl.py -c COM4 --send 600 0100000000000000

Usage as a transport:
    from zqwl import ZqwlBus, find_zqwl_port
    bus = ZqwlBus("COM4", bitrate=500000)

Pure ASCII on purpose (project convention).
"""

import argparse
import os
import queue
import sys
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except Exception as exc:  # pragma: no cover
    print("ERROR: pyserial is required (%s). Install with: make host-deps" % exc)
    raise SystemExit(2)

try:
    import can
except Exception:  # pragma: no cover
    can = None

ZQWL_VID = 0x3562

FRAME_HEAD = 0x5A
FRAME_TAIL = 0xA5
CFG_HEAD0 = 0x49
CFG_HEAD1 = 0x3B
CFG_TAIL0 = 0x45
CFG_TAIL1 = 0x2E
HB_LEN = 17
CFG_LEN = 22
CFG_FUNC_SET_CAN = 0x42
CFG_FUNC_SYSCTRL = 0x44
CFG_READ = 0x52
CFG_WRITE = 0x57

# arbitration-domain bitrate -> 4-bit code (vendor table)
ARB_CODES = {
    1000000: 0x0, 800000: 0x1, 500000: 0x2, 400000: 0x3, 250000: 0x4,
    200000: 0x5, 125000: 0x6, 100000: 0x7, 50000: 0x8, 40000: 0x9,
    25000: 0xA, 20000: 0xB, 15000: 0xC, 10000: 0xD, 5000: 0xE,
}
# data-domain bitrate -> 4-bit code (only meaningful for CAN FD)
DATA_CODES = {
    5000000: 0x0, 4000000: 0x1, 2000000: 0x2, 1000000: 0x3, 800000: 0x4,
    500000: 0x5, 400000: 0x6, 250000: 0x7, 200000: 0x8, 125000: 0x9,
    100000: 0xA,
}

BUS_STATES = {0: "normal", 1: "warning", 2: "error-passive", 3: "bus-off"}


class ZqwlError(Exception):
    pass


def find_zqwl_port():
    """Return the COM port of a ZQWL adapter (VID 0x3562), or None."""
    for port in list_ports.comports():
        if getattr(port, "vid", None) == ZQWL_VID:
            return port.device
    return None


def list_zqwl_ports():
    out = []
    for port in list_ports.comports():
        if getattr(port, "vid", None) == ZQWL_VID:
            out.append((port.device, port.description or ""))
    return out


def baud_code(bitrate):
    arb = ARB_CODES.get(bitrate)
    if arb is None:
        raise ZqwlError("bitrate %d is not in the vendor table; use a custom "
                        "baud or one of %s" % (bitrate, sorted(ARB_CODES)))
    data = DATA_CODES.get(bitrate, 0x0)
    return (arb << 4) | data


class Heartbeat(object):
    """Decoded 17-byte device heartbeat."""

    def __init__(self, raw):
        self.raw = raw
        # Empirically located on a ZQWL-UCANFD-100C: the byte that carries the
        # channel/bus status is at index 10 (it reads 0x20 while CAN0 is open ->
        # bit5 = CAN0 enable flag, matching the manual's bit table).  Index 2
        # stays 0x00 in both states, so the manual's "status byte right after the
        # function code" wording does not match this firmware revision.
        # raw.hex() is logged too, so another model can be decoded without
        # guessing, and nothing here is load-bearing for CAN-OTA itself.
        self.status = raw[10] if len(raw) > 10 else 0
        self.can0_enabled = bool(self.status & 0x20)
        self.can1_enabled = bool(self.status & 0x10)
        self.can0_state = (self.status >> 2) & 0x3
        self.can1_state = self.status & 0x3
        # remaining bytes are rates and error counters; keep them raw
        self.tail = raw[2:]

    def text(self):
        return ("device: CAN0 %s (%s) CAN1 %s (%s)"
                % ("on" if self.can0_enabled else "off",
                   BUS_STATES.get(self.can0_state, "?"),
                   "on" if self.can1_enabled else "off",
                   BUS_STATES.get(self.can1_state, "?")))


class ZqwlBus(object):
    """python-can compatible bus for a ZQWL USBCANFD adapter.

    send(can.Message) / recv(timeout) -> can.Message / shutdown()
    """

    def __init__(self, channel, bitrate=500000, can_channel=0,
                 timeout=0.05, configure=True, verbose=False):
        self.channel = channel
        self.bitrate = bitrate
        self.can_channel = can_channel
        self.verbose = verbose
        self.last_heartbeat = None
        self.last_transport_activity = 0.0
        self.heartbeats = 0
        self.cfg_replies = []
        self.tx_frames = 0
        self.rx_frames = 0
        self.dropped = 0
        self.other_frames = 0
        self._rx = queue.Queue()
        self._stop = False
        self._lock = threading.Lock()

        # CDC virtual port: the vendor says the baud is auto-matched, but the
        # adapter uses it for its internal buffering, and at 115200 the host can
        # outrun it during a bulk transfer (measured: occasional dropped frames).
        # ZQWL_SERIAL_BAUD / ZQWL_TX_GAP_MS can be tuned without editing code.
        self.serial_baud = int(os.environ.get("ZQWL_SERIAL_BAUD", "3000000"))
        self.tx_gap = float(os.environ.get("ZQWL_TX_GAP_MS", "0.3")) / 1000.0
        try:
            self.ser = serial.Serial(channel, self.serial_baud, timeout=0.02,
                                     write_timeout=1.0)
        except Exception as exc:
            raise ZqwlError("cannot open %s: %s" % (channel, exc))

        self._thread = threading.Thread(target=self._reader, name="zqwl-rx")
        self._thread.daemon = True
        self._thread.start()

        if configure:
            self.configure(bitrate)

    # -- configuration -----------------------------------------------------
    def _cfg_frame(self, func, rw, data):
        body = bytearray(16)
        for i, b in enumerate(data[:16]):
            body[i] = b
        return bytes([CFG_HEAD0, CFG_HEAD1, func, rw]) + bytes(body) + \
            bytes([CFG_TAIL0, CFG_TAIL1])

    def configure(self, bitrate=None):
        if bitrate:
            self.bitrate = bitrate
        code = baud_code(self.bitrate)
        # channel, custom-baud flag = 0 (use the vendor table), baud code
        self._write(self._cfg_frame(CFG_FUNC_SET_CAN, CFG_WRITE,
                                    [self.can_channel & 0xFF, 0x00, code]))
        # apply, no reset, enable our channel and disable the other one.
        # data[0] must be 0x01: the manual says 0x00 = "not effective", and with
        # 0x00 the channel never comes up (no heartbeat).  It also saves the
        # config to the adapter's flash, which is what the vendor tool does.
        enables = [0x00, 0x00]
        enables[self.can_channel if self.can_channel < 2 else 0] = 0x01
        self._write(self._cfg_frame(CFG_FUNC_SYSCTRL, CFG_WRITE,
                                    [0x01, 0x00] + enables))
        # Wait for TWO heartbeats plus a settle delay.  Measured on the ZQWL
        # UCANFD-100C: the first one or two CAN frames sent right after the
        # channel is opened are silently dropped, which makes a single-shot
        # GET_INFO look like "device does not answer" when it is perfectly fine.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self.heartbeats < 2:
            time.sleep(0.02)
        time.sleep(0.15)
        if self.heartbeats == 0:
            raise ZqwlError("no heartbeat from %s after configuring CAN%d at %d "
                            "bit/s -- wrong port, or the adapter is not a ZQWL "
                            "device" % (self.channel, self.can_channel, self.bitrate))
        return self.last_heartbeat

    def _write(self, blob):
        with self._lock:
            try:
                self.ser.write(blob)
                self.ser.flush()
            except Exception as exc:
                raise ZqwlError("serial write failed: %s" % exc)
        if self.verbose:
            print("   [zqwl] tx %s" % blob.hex())

    # -- reader ------------------------------------------------------------
    def _reader(self):
        buf = bytearray()
        while not self._stop:
            try:
                chunk = self.ser.read(256)
            except Exception:
                break
            if not chunk:
                continue
            self.last_transport_activity = time.time()
            buf += chunk
            while buf:
                head = buf[0]
                if head == FRAME_HEAD:
                    if len(buf) < 2:
                        break
                    # BYTE1 == 0xFF (or 0xFE on 4-channel models) marks a
                    # heartbeat, NOT BYTE0 -- reading the manual's "BYTE1 为
                    # 0xFF 时为心跳包" as byte0 was the first bug in this tool.
                    if buf[1] in (0xFF, 0xFE):
                        if len(buf) < HB_LEN:
                            break
                        hb = bytes(buf[:HB_LEN])
                        del buf[:HB_LEN]
                        self.heartbeats += 1
                        self.last_heartbeat = Heartbeat(hb)
                        continue
                    dlc = buf[1] & 0x0F
                    total = 8 + dlc
                    if len(buf) < total:
                        break
                    frame = bytes(buf[:total])
                    del buf[:total]
                    if frame[-1] != FRAME_TAIL:
                        self.dropped += 1
                        continue
                    self._on_can(frame)
                elif head == CFG_HEAD0:
                    if len(buf) < CFG_LEN:
                        break
                    cfg = bytes(buf[:CFG_LEN])
                    del buf[:CFG_LEN]
                    self.cfg_replies.append(cfg)
                    if self.verbose:
                        print("   [zqwl] cfg reply %s" % cfg.hex())
                else:
                    del buf[:1]  # resync
                    self.dropped += 1

    def _on_can(self, frame):
        dlc = frame[1] & 0x0F
        info2 = frame[2]
        extended = bool(info2 & 0x04)
        remote = bool(info2 & 0x02)
        if extended:
            arb = (((frame[3] & 0x1F) << 24) | (frame[4] << 16) |
                   (frame[5] << 8) | frame[6]) & 0x1FFFFFFF
        else:
            arb = ((frame[5] << 8) | frame[6]) & 0x7FF
        data = bytes(frame[7:7 + dlc])
        if can is not None:
            msg = can.Message(arbitration_id=arb, is_extended_id=extended,
                              is_remote_frame=remote, data=data, dlc=dlc)
        else:  # pragma: no cover
            msg = type("Msg", (), {"arbitration_id": arb, "data": data,
                                   "dlc": dlc, "is_extended_id": extended})()
        self.rx_frames += 1
        self._rx.put(msg)

    # -- python-can style API ---------------------------------------------
    def send(self, msg, timeout=None):
        data = bytes(msg.data) if msg.data else b""
        dlc = len(data)
        if dlc > 8:
            raise ZqwlError("classic CAN frames carry at most 8 bytes")
        arb = msg.arbitration_id
        if getattr(msg, "is_extended_id", False):
            id_bytes = bytes([(arb >> 24) & 0x1F, (arb >> 16) & 0xFF,
                              (arb >> 8) & 0xFF, arb & 0xFF])
            info2 = 0x04
        else:
            id_bytes = bytes([0x00, 0x00, (arb >> 8) & 0x7F, arb & 0xFF])
            info2 = 0x00
        if getattr(msg, "is_remote_frame", False):
            info2 |= 0x02
        # BYTE2 bits[7:6] = send type: 00 normal, 01 single-shot.
        # Single-shot on purpose: the adapter's normal mode retransmits a frame
        # whose ACK it missed, which delivers DUPLICATES to the device and
        # corrupts its DATA-frame counting (and leaves late NACKs that get
        # mistaken for the answer to the next command).  Lost frames are already
        # recovered by our SYNC/NACK scheme, so single-shot is strictly better.
        info2 |= 0x40
        info1 = dlc & 0x0F
        frame = bytes([FRAME_HEAD, info1, info2]) + id_bytes + data + bytes([FRAME_TAIL])
        self._write(frame)
        self.tx_frames += 1
        # small pacing gap: gives the adapter time to push the frame onto the
        # bus and to emit its per-frame heartbeat ack
        if self.tx_gap > 0.0:
            time.sleep(self.tx_gap)

    def recv(self, timeout=None):
        if timeout is None:
            timeout = 0.05
        try:
            return self._rx.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def shutdown(self):
        self._stop = True
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass

    # some callers read bus.state (python-can API)
    @property
    def state(self):
        hb = self.last_heartbeat
        if hb is None:
            return None
        return BUS_STATES.get(hb.can0_state, "?")

    def stats(self):
        return ("zqwl %s CAN%d @%d: tx=%d rx=%d hb=%d dropped=%d"
                % (self.channel, self.can_channel, self.bitrate,
                   self.tx_frames, self.rx_frames, self.heartbeats, self.dropped))


# ---------------------------------------------------------------------------
def open_any(channel=None, bitrate=500000, can_channel=0, verbose=False):
    """Open the given port, or auto-detect the ZQWL adapter."""
    port = channel
    if port is None or not str(port).upper().startswith("COM"):
        port = find_zqwl_port()
    if port is None:
        raise ZqwlError("no ZQWL adapter found (VID 0x%04X); pass --channel COMx"
                        % ZQWL_VID)
    return ZqwlBus(port, bitrate=bitrate, can_channel=can_channel, verbose=verbose)


def main():
    ap = argparse.ArgumentParser(description="ZQWL USBCANFD bus check")
    ap.add_argument("-c", "--channel", default=None, help="COM port (default: auto)")
    ap.add_argument("-b", "--baud", type=int, default=500000, help="CAN bitrate")
    ap.add_argument("--can-channel", type=int, default=0, help="CAN0..CAN3")
    ap.add_argument("--listen", type=float, default=0.0, help="listen seconds")
    ap.add_argument("--send", nargs=2, metavar=("ID", "HEXDATA"),
                    help="send one frame, e.g. --send 600 0100000000000000")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    ports = list_zqwl_ports()
    print("ZQWL adapters found: %s" % (ports if ports else "none (VID 0x%04X)" % ZQWL_VID))

    bus = None
    try:
        bus = open_any(args.channel, args.baud, args.can_channel, args.verbose)
    except ZqwlError as exc:
        print("ERROR: %s" % exc)
        return 2

    print("opened %s, CAN%d @ %d bit/s, baud code 0x%02X, serial %d, tx_gap %.1f ms"
          % (bus.channel, bus.can_channel, bus.bitrate, baud_code(bus.bitrate),
             bus.serial_baud, bus.tx_gap * 1000.0))
    if bus.last_heartbeat is not None:
        print("heartbeat: %s" % bus.last_heartbeat.text())

    rc = 0
    try:
        if args.send:
            arb = int(args.send[0], 16)
            data = bytes.fromhex(args.send[1])
            msg = can.Message(arbitration_id=arb, is_extended_id=False, data=data)
            bus.send(msg)
            print("sent id=0x%03X dlc=%d %s" % (arb, len(data), data.hex()))
            time.sleep(0.15)

        if args.listen > 0.0:
            print("listening %.1f s ..." % args.listen)
            deadline = time.monotonic() + args.listen
            got = 0
            while time.monotonic() < deadline:
                msg = bus.recv(timeout=0.2)
                if msg is not None:
                    got += 1
                    print("  rx id=0x%03X dlc=%d %s"
                          % (msg.arbitration_id, msg.dlc, msg.data.hex()))
            print("received %d frame(s)" % got)
            hb = bus.last_heartbeat
            if hb is not None:
                print("heartbeat: %s   raw tail %s" % (hb.text(), hb.tail.hex()))
    finally:
        print(bus.stats())
        bus.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
