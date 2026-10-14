#!/usr/bin/env python3
"""Transfer files to/from Motorola P2K phones (RAZR V3r etc.) over a USB cable.

Protocol based on libp2kmoto 0.1 (Dmitry Nezhevenko, GPL) and p2k-core.

  ./p2k ls [filter]             list files (filter = path substring, e.g. picture)
  ./p2k get <path> [dest]       download a file
  ./p2k pull <filter> <dir>     download every file whose path contains filter
  ./p2k put <file> <path>       upload a file to the phone (e.g. /c/mobile/audio/x.mp3)
  ./p2k df                      free space per volume
  ./p2k mode                    just switch the phone to P2K mode
"""
# SPDX-License-Identifier: GPL-2.0-or-later
import glob
import os
import sys
import time

import usb.backend.libusb1
import usb.core
import usb.util

VID = 0x22B8
PID_AT = 0x4902
PID_P2K = 0x4901
IFACE = 0x08
CHUNK = 0x400


class P2KError(Exception):
    pass


class ProtocolLost(P2KError):
    """The phone stopped answering or the reply stream is out of sync."""


def _backend():
    for lib in ("/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib"):
        if os.path.exists(lib):
            return usb.backend.libusb1.get_backend(find_library=lambda _, lib=lib: lib)
    return None


BACKEND = _backend()


def find_dev(pid):
    return usb.core.find(idVendor=VID, idProduct=pid, backend=BACKEND)


def switch_to_p2k():
    """Send AT+MODE=8 to the phone's modem port."""
    import termios
    ports = glob.glob("/dev/cu.usbmodem*")
    for port in ports:
        try:
            fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            a = termios.tcgetattr(fd)
            a[0] = 0; a[1] = 0; a[3] = 0
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            a[4] = a[5] = termios.B115200
            termios.tcsetattr(fd, termios.TCSANOW, a)
            os.write(fd, b"AT+CGMI\r")
            time.sleep(0.5)
            try:
                resp = os.read(fd, 4096)
            except BlockingIOError:
                resp = b""
            if b"Motorola" not in resp:
                continue
            os.write(fd, b"AT+MODE=8\r")
            time.sleep(0.5)
            return True
        finally:
            os.close(fd)
    return False


class Phone:
    def __init__(self):
        dev = find_dev(PID_P2K)
        if dev is None:
            if find_dev(PID_AT) is None:
                raise P2KError("Phone not found. Connect the USB cable.")
            print("Switching phone to P2K mode...")
            if not switch_to_p2k():
                raise P2KError("Could not send AT+MODE=8")
            for _ in range(20):
                time.sleep(0.5)
                dev = find_dev(PID_P2K)
                if dev is not None:
                    break
            else:
                raise P2KError("Phone did not switch to P2K mode")
            time.sleep(1)
        self.dev = dev
        try:
            if dev.get_active_configuration().bConfigurationValue != 1:
                dev.set_configuration(1)
        except usb.core.USBError:
            dev.set_configuration(1)
        usb.util.claim_interface(dev, IFACE)
        self.pid = 0

    # --- low level -------------------------------------------------------
    def _send(self, data):
        self.pid = (self.pid + 1) & 0xFFF
        data = bytearray(data)
        data[0] = self.pid >> 8
        data[1] = self.pid & 0xFF
        self.dev.ctrl_transfer(0x41, 0x02, 0, IFACE, bytes(data), 3000)

    def _recv(self):
        t = time.time()
        while True:
            r = bytes(self.dev.ctrl_transfer(0xC1, 0x00, 0, IFACE, 8, 3000))
            if len(r) > 2:
                break
            if time.time() - t > 5:
                raise ProtocolLost("Phone did not respond")
            time.sleep(0.001)
        cnt = int.from_bytes(r[0:2], "big")
        size = int.from_bytes(r[2:4], "big")
        bufsize = 2 * cnt + size + 4
        buf = bytes(self.dev.ctrl_transfer(0xC1, 0x01, 1, IFACE, bufsize, 3000))
        # reply header: buf[6:8] id, buf[8] type (0x80 = with data, 0x60 = without),
        # buf[10:12] data length, buf[14] error code, then data
        pid = int.from_bytes(buf[6:8], "big") & 0xFFF
        if pid != self.pid:
            raise ProtocolLost("Reply to the wrong packet (%x != %x)" % (pid, self.pid))
        return buf

    def cmd(self, code, payload=b""):
        hdr = bytearray(8)
        hdr[2:4] = code.to_bytes(2, "big")
        hdr[4:6] = len(payload).to_bytes(2, "big")
        self._send(bytes(hdr) + payload)
        return self._recv()

    def fs(self, sub, args=b""):
        return self.cmd(0x4A, sub.to_bytes(4, "big") + args)

    @staticmethod
    def _check(buf, what):
        if buf[8] == 0x80 and len(buf) > 14 and buf[14] != 0:
            raise P2KError("%s: phone error 0x%02x" % (what, buf[14]))

    # --- file system ---------------------------------------------------
    def volumes(self):
        """Volume names, e.g. ['/a', '/c'] (UTF-16BE, separated by U+FFFE)."""
        text = self.fs(0x0A)[14:].decode("utf-16-be", errors="ignore").split("\x00", 1)[0]
        return [v.strip() for v in text.split("\ufffe") if v.strip()]

    def free_space(self, volume):
        """Free bytes on a volume ('/c' or '/c/')."""
        arg = b"\0\0" + volume.rstrip("/").encode("utf-16-be") + b"\0\0"
        buf = self.fs(0x0B, arg + b"\0" * (0xFC - len(arg)))
        if buf[8] != 0x80:
            raise P2KError("free space %s: phone error 0x%02x" % (volume, buf[8]))
        return int.from_bytes(buf[14:18], "big")

    def list_files(self, volumes=("/a/", "/b/", "/c/")):
        files = []
        for vol in volumes:
            pat = vol.encode("utf-16-be") + b"\xff\xfe" + "*".encode("utf-16-be")
            buf = self.fs(0x07, pat + b"\0" * (0xFC - len(pat)))
            if buf[8] != 0x80:
                continue  # no such volume (e.g. no memory card)
            files += self._read_list(int.from_bytes(buf[14:16], "big"))
        return files

    def _read_list(self, total):
        files = []
        while len(files) < total:
            buf = self.fs(0x08)
            self._check(buf, "list")
            n = buf[0x0F] | (buf[0x10] << 8)
            if n == 0:
                break
            body = buf[0x12:]
            if len(body) % n:
                raise P2KError("Unexpected file list format: %d / %d" % (len(body), n))
            rec = len(body) // n
            fn = rec - 8
            for i in range(n):
                r = body[i * rec:(i + 1) * rec]
                name = r[:fn].split(b"\x00", 1)[0].decode("latin-1")
                attr = r[fn + 1]
                size = int.from_bytes(r[fn + 4:fn + 8], "big")
                files.append((name, size, attr))
        return files

    @staticmethod
    def _ok(buf, what):
        # 0x60 = done, no data; 0x80 = data, first byte is a status (0 = ok); 0x50/0x70 = rejected
        if buf[8] == 0x60 or (buf[8] == 0x80 and len(buf) > 14 and buf[14] == 0):
            return
        code = buf[14] if buf[8] == 0x80 and len(buf) > 14 else buf[8]
        raise P2KError("%s: phone error 0x%02x" % (what, code))

    def open(self, path, attr=0):
        self._ok(self.fs(0x00, attr.to_bytes(4, "big") + path.encode("latin-1")), "open " + path)

    def close(self):
        self._ok(self.fs(0x04), "close")

    def read(self, size):
        buf = self.fs(0x01, size.to_bytes(4, "big"))
        if buf[8] != 0x80:
            raise P2KError("read: phone error 0x%02x" % buf[8])
        n = int.from_bytes(buf[10:12], "big")
        if n != size:
            raise P2KError("read: got %d bytes instead of %d" % (n, size))
        return buf[0x0E:0x0E + size]

    def write(self, data):
        self._ok(self.fs(0x02, len(data).to_bytes(4, "big") + data), "write")

    def delete(self, path):
        self._ok(self.fs(0x05, path.encode("latin-1")), "delete " + path)

    def read_all(self, path, size, attr, on_progress=None):
        out = bytearray()
        self.open(path, attr)
        try:
            while len(out) < size:
                out += self.read(min(CHUNK, size - len(out)))
                if on_progress:
                    on_progress(len(out), size)
        except Exception:
            self._close_quietly()
            raise
        self.close()
        return bytes(out)

    def write_all(self, path, data, on_progress=None):
        self.open(path, 0)
        try:
            for i in range(0, len(data), CHUNK):
                self.write(data[i:i + CHUNK])
                if on_progress:
                    on_progress(min(i + CHUNK, len(data)), len(data))
        except Exception:
            self._close_quietly()
            raise
        self.close()

    def _close_quietly(self):
        try:
            self.close()
        except (P2KError, usb.core.USBError):
            pass

    def release(self):
        try:
            usb.util.dispose_resources(self.dev)
        except usb.core.USBError:
            pass

    def download(self, path, size, attr, dest):
        data = self.read_all(path, size, attr, progress)
        with open(dest, "wb") as f:
            f.write(data)
        print()

    def upload(self, src, path):
        with open(src, "rb") as f:
            data = f.read()
        self.write_all(path, data, progress)
        print()


def progress(done, total):
    w = 30
    k = int(w * done / total) if total else w
    sys.stdout.write("\r  [%s%s] %d/%d" % ("#" * k, "." * (w - k), done, total))
    sys.stdout.flush()


def human(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024


def find(files, path):
    for f in files:
        if f[0] == path:
            return f
    raise P2KError("No such file on the phone: " + path)


def main(argv):
    if len(argv) < 1 or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    op = argv[0]
    ph = Phone()
    if op == "mode":
        print("Phone is in P2K mode.")
        return 0
    if op == "volumes":
        print(ph.volumes())
        return 0
    if op == "df":
        files = ph.list_files()
        for vol in ph.volumes():
            used = sum(size for name, size, _ in files if name.startswith(vol + "/"))
            print("%-3s  free %9s   used by files %9s" % (vol, human(ph.free_space(vol)), human(used)))
        return 0
    files = ph.list_files()
    if op == "ls":
        flt = argv[1].lower() if len(argv) > 1 else ""
        for name, size, attr in files:
            if flt in name.lower():
                print("%10d  %s" % (size, name))
    elif op == "get":
        name, size, attr = find(files, argv[1])
        dest = argv[2] if len(argv) > 2 else os.path.basename(name)
        if os.path.isdir(dest):
            dest = os.path.join(dest, os.path.basename(name))
        print(name, "->", dest)
        ph.download(name, size, attr, dest)
    elif op == "pull":
        flt, outdir = argv[1].lower(), argv[2]
        os.makedirs(outdir, exist_ok=True)
        for name, size, attr in files:
            if flt in name.lower():
                dest = os.path.join(outdir, os.path.basename(name))
                print(name, "->", dest)
                ph.download(name, size, attr, dest)
    elif op == "put":
        src, path = argv[1], argv[2]
        if path.endswith("/"):
            path += os.path.basename(src)
        print(src, "->", path)
        ph.upload(src, path)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except P2KError as e:
        print("Error:", e)
        sys.exit(1)
