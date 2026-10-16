#!/usr/bin/env python3
"""
elo-touch-fix - one-click calibration for Elo AccuTouch touchscreens
(e.g. Elo 2216 USB controller / TS15R panel) on Debian, X11 or Wayland.

On Wayland (or with --udev) it reads the raw device instead and installs a
udev LIBINPUT_CALIBRATION_MATRIX rule - see the "Wayland / udev mode" section.

How it works
  1. Finds the Elo touch device with `xinput`.
  2. Resets its "Coordinate Transformation Matrix" to identity, so we see the
     raw (uncalibrated) position of each touch.
  3. Shows a full-screen window with 4 corner targets plus a centre target.
  4. Fits an affine matrix (handles flip, rotation, scale and offset) by least
     squares and applies it straight away.
  5. Saves the matrix and adds an autostart entry so it is re-applied at every
     login. Run with `--system` to also write an Xorg config (needs sudo).

Usage
  fix.py                 calibrate (default)
  fix.py apply           re-apply the saved calibration
  fix.py flip            quick fix: just invert the Y axis
  fix.py reset           remove calibration (identity matrix)
  fix.py status          show device and current matrix
  options: --device "Name"  --system  --udev
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

PROP = "Coordinate Transformation Matrix"
LIBINPUT_PROP = "libinput Calibration Matrix"
IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1]
FLIP_Y = [1, 0, 0, 0, -1, 1, 0, 0, 1]

CONFIG_DIR = os.path.expanduser("~/.config/elo-touch-fix")
MATRIX_FILE = os.path.join(CONFIG_DIR, "matrix")
DEVICE_FILE = os.path.join(CONFIG_DIR, "device")
AUTOSTART = os.path.expanduser("~/.config/autostart/elo-touch-fix-apply.desktop")
XORG_CONF = "/etc/X11/xorg.conf.d/99-elo-touch-calibration.conf"

# Matched case-insensitively against `xinput list --name-only`.
DEVICE_PATTERNS = [r"accutouch", r"\belo\b", r"elo ?touch", r"touch ?systems"]


def die(msg):
    print("error: " + msg, file=sys.stderr)
    notify(msg)
    sys.exit(1)


def notify(msg):
    """Pop up a desktop notification when launched by double-click."""
    if not sys.stdout.isatty() and shutil.which("notify-send"):
        subprocess.call(["notify-send", "Elo touch fix", msg])


def run(*args):
    return subprocess.run(args, capture_output=True, text=True)


# --------------------------------------------------------------------------
# xinput helpers
# --------------------------------------------------------------------------

def check_session():
    if not shutil.which("xinput"):
        die("xinput not found. Install it with: sudo apt install xinput")
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        die("This is a Wayland session. Log out, choose the 'Xorg' session "
            "on the login screen (gear icon), and run this again.")
    if not os.environ.get("DISPLAY"):
        die("No X display found (DISPLAY is not set).")


def find_device(name=None):
    names = [n.strip() for n in run("xinput", "list", "--name-only").stdout.splitlines()]
    if name:
        if name in names:
            return name
        die("device %r not found. Devices:\n  %s" % (name, "\n  ".join(names)))
    if os.path.exists(DEVICE_FILE):
        saved = open(DEVICE_FILE).read().strip()
        if saved in names:
            return saved
    for pat in DEVICE_PATTERNS:
        for n in names:
            if re.search(pat, n, re.I) and PROP in run("xinput", "list-props", n).stdout:
                return n
    die("no Elo touchscreen found. Check `xinput list` and `lsusb` (vendor 04e7), "
        "then pass --device \"Name\". Devices seen:\n  " + "\n  ".join(names))


def get_matrix(dev, prop=PROP):
    out = run("xinput", "list-props", dev).stdout
    m = re.search(re.escape(prop) + r" \(\d+\):\s*(.*)", out)
    if not m:
        return None
    return [float(v) for v in m.group(1).split(",")]


def set_matrix(dev, matrix, prop=PROP):
    r = run("xinput", "set-prop", dev, prop, *["%.6f" % v for v in matrix])
    if r.returncode != 0:
        die("xinput set-prop failed: " + r.stderr.strip())


def fmt(matrix):
    return " ".join("%.6f" % v for v in matrix)


# --------------------------------------------------------------------------
# Maths: least-squares affine fit
# --------------------------------------------------------------------------

def solve3(A, b):
    """Solve a 3x3 linear system with Cramer's rule."""
    def det(M):
        return (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
                - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
                + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
    d = det(A)
    if abs(d) < 1e-12:
        raise ValueError("touch points are degenerate (all in a line?)")
    out = []
    for i in range(3):
        M = [row[:] for row in A]
        for r in range(3):
            M[r][i] = b[r]
        out.append(det(M) / d)
    return out


def fit_affine(raw, target):
    """raw/target: lists of (x, y) in normalised 0..1 coordinates.
    Returns a 9-element row-major matrix mapping raw -> target."""
    rows = [(x, y, 1.0) for x, y in raw]
    AtA = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    abc = solve3(AtA, [sum(r[i] * t[0] for r, t in zip(rows, target)) for i in range(3)])
    def_ = solve3(AtA, [sum(r[i] * t[1] for r, t in zip(rows, target)) for i in range(3)])
    return abc + def_ + [0.0, 0.0, 1.0]


def apply_affine(m, x, y):
    return m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]


# --------------------------------------------------------------------------
# Calibration UI
# --------------------------------------------------------------------------

def collect_touches():
    """Full-screen window with targets. Returns (raw_points, target_points,
    root_w, root_h) in root-window pixels, or None if cancelled."""
    try:
        import tkinter as tk
    except ImportError:
        die("python3-tk not installed. Install it with: sudo apt install python3-tk")

    root = tk.Tk()
    root.title("Elo touch calibration")
    root.attributes("-fullscreen", True)
    root.configure(background="black", cursor="crosshair")
    root.update_idletasks()
    root.update()

    W, H = root.winfo_screenwidth(), root.winfo_screenheight()   # whole X screen
    ox, oy = root.winfo_rootx(), root.winfo_rooty()               # this monitor
    w, h = root.winfo_width(), root.winfo_height()

    inset = 0.10
    fractions = [(inset, inset), (1 - inset, inset), (1 - inset, 1 - inset),
                 (inset, 1 - inset), (0.5, 0.5)]
    targets = [(ox + fx * w, oy + fy * h) for fx, fy in fractions]
    raw = []

    canvas = tk.Canvas(root, background="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    def draw():
        canvas.delete("all")
        i = len(raw)
        canvas.create_text(w / 2, h * 0.30, fill="white", font=("Sans", 18),
                           text="Touch the centre of the red cross and lift your finger.\n"
                                "Use a fingernail or stylus for accuracy.\n"
                                "Esc = cancel     Point %d of %d" % (i + 1, len(targets)),
                           justify="center")
        tx, ty = targets[i][0] - ox, targets[i][1] - oy
        r = 25
        canvas.create_line(tx - r, ty, tx + r, ty, fill="red", width=3)
        canvas.create_line(tx, ty - r, tx, ty + r, fill="red", width=3)
        canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, outline="white", width=2)

    result = {"ok": False}

    def on_release(event):
        # Pointer position in root coords == raw touch position, since the
        # matrix is identity while we calibrate.
        raw.append((event.x_root, event.y_root))
        if len(raw) == len(targets):
            result["ok"] = True
            root.destroy()
        else:
            draw()

    def on_escape(_event):
        root.destroy()

    root.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)
    draw()
    root.focus_force()
    # Grab the pointer so touches landing "off-window" (because the raw
    # mapping is way off) still reach us.
    root.after(200, lambda: _try_grab(root))
    root.after(120000, root.destroy)  # give up after 2 minutes
    root.mainloop()

    if not result["ok"]:
        return None
    return raw, targets, W, H


def _try_grab(root):
    try:
        root.grab_set_global()
    except Exception:
        pass


def calibrate(dev):
    old = get_matrix(dev) or IDENTITY
    old_li = get_matrix(dev, LIBINPUT_PROP)

    set_matrix(dev, IDENTITY)
    if old_li is not None:
        set_matrix(dev, IDENTITY, LIBINPUT_PROP)

    try:
        res = collect_touches()
    except Exception as e:
        res = None
        print("calibration window failed: %s" % e, file=sys.stderr)

    if res is None:
        set_matrix(dev, old)
        if old_li is not None:
            set_matrix(dev, old_li, LIBINPUT_PROP)
        die("Calibration cancelled; previous settings restored.")

    raw_px, tgt_px, W, H = res
    raw = [(x / W, y / H) for x, y in raw_px]
    tgt = [(x / W, y / H) for x, y in tgt_px]
    try:
        m = fit_affine(raw, tgt)
    except ValueError as e:
        set_matrix(dev, old)
        die("Calibration failed: %s. Please try again." % e)

    # Sanity check: worst-case error in pixels.
    err = max(((apply_affine(m, *r)[0] - t[0]) * W) ** 2 +
              ((apply_affine(m, *r)[1] - t[1]) * H) ** 2 for r, t in zip(raw, tgt)) ** 0.5
    print("raw touches  : %s" % ["(%.0f,%.0f)" % p for p in raw_px])
    print("matrix       : %s" % fmt(m))
    print("max error    : %.1f px" % err)
    if err > 0.05 * max(W, H):
        set_matrix(dev, old)
        die("Touches were inconsistent (error %.0f px). Previous settings "
            "restored - please run again and touch carefully." % err)

    set_matrix(dev, m)
    save(dev, m)
    return m, err


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def save(dev, m):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(MATRIX_FILE, "w") as f:
        f.write(fmt(m) + "\n")
    with open(DEVICE_FILE, "w") as f:
        f.write(dev + "\n")

    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Elo touch calibration\n"
                "Exec=%s %s apply\nX-GNOME-Autostart-Delay=3\nNoDisplay=true\n"
                % (sys.executable, os.path.abspath(__file__)))


def load():
    if not os.path.exists(MATRIX_FILE):
        die("no saved calibration yet - run the calibration first.")
    return [float(v) for v in open(MATRIX_FILE).read().split()]


def write_system_conf(dev, m):
    """Xorg config so the calibration applies on the login screen too."""
    conf = ('# Written by elo-touch-fix\n'
            'Section "InputClass"\n'
            '    Identifier "Elo touch calibration"\n'
            '    MatchProduct "%s"\n'
            '    MatchIsTouchscreen "on"\n'
            '    Option "TransformationMatrix" "%s"\n'
            'EndSection\n') % (dev, fmt(m))
    tmp = os.path.join(CONFIG_DIR, "xorg.conf.tmp")
    with open(tmp, "w") as f:
        f.write(conf)
    elevate = ["pkexec"] if shutil.which("pkexec") and os.environ.get("DISPLAY") else ["sudo"]
    cmd = elevate + ["sh", "-c", 'mkdir -p "$(dirname "$2")" && install -m 644 "$1" "$2"',
                     "sh", tmp, XORG_CONF]
    if subprocess.call(cmd) == 0:
        print("wrote " + XORG_CONF)
    else:
        print("could not write %s (permission denied?)" % XORG_CONF, file=sys.stderr)


# --------------------------------------------------------------------------
# Wayland / udev mode
#
# Wayland compositors (GNOME, KDE, wlroots...) all use libinput, which reads
# the calibration from the udev property LIBINPUT_CALIBRATION_MATRIX. We read
# raw touches straight from /dev/input/eventN (needs root, so a small helper
# runs under pkexec), fit the matrix, write a udev rule and re-plug the
# device so the compositor picks it up. This mode also works under X11
# when the xserver-xorg-input-libinput driver is in use.
# --------------------------------------------------------------------------

UDEV_RULE = "/etc/udev/rules.d/99-elo-touch-calibration.rules"
EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y = 0x00, 0x01
BTN_LEFT, BTN_TOUCH = 0x110, 0x14a
EVENT_FMT = "llHHi"  # struct input_event (native long = time_t on this ABI)


def find_event_device(name=None):
    """Returns (name, /dev/input/eventN) from /proc/bus/input/devices."""
    blocks = open("/proc/bus/input/devices").read().strip().split("\n\n")
    found = []
    for b in blocks:
        n = re.search(r'^N: Name="(.*)"', b, re.M)
        h = re.search(r"^H: Handlers=.*\b(event\d+)\b", b, re.M)
        if not (n and h):
            continue
        dev_name = n.group(1)
        is_elo = re.search(r"Vendor=04e7", b, re.I) or any(
            re.search(p, dev_name, re.I) for p in DEVICE_PATTERNS)
        abs_ = re.search(r"^B: ABS=(\w+)", b, re.M)
        has_xy = abs_ and int(abs_.group(1), 16) & 0x3 == 0x3
        if (dev_name == name) if name else (is_elo and has_xy):
            found.append((dev_name, "/dev/input/" + h.group(1)))
    if not found:
        die("no Elo touchscreen found in /proc/bus/input/devices. Check that "
            "`lsusb` shows vendor 04e7, or pass --device \"Name\".")
    return found[0]


def helper(path):
    """Runs as root. Streams normalised touch-up positions to stdout and
    executes write/remove commands from stdin."""
    import fcntl
    import select
    import struct

    def absinfo(fd, axis):
        req = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + axis)  # EVIOCGABS
        buf = bytearray(24)
        fcntl.ioctl(fd, req, buf)
        return struct.unpack("6i", buf)[1:3]  # min, max

    def say(*words):
        sys.stdout.write(" ".join(str(w) for w in words) + "\n")
        sys.stdout.flush()

    fd = os.open(path, os.O_RDONLY)
    (xmin, xmax), (ymin, ymax) = absinfo(fd, ABS_X), absinfo(fd, ABS_Y)
    say("ready", xmin, xmax, ymin, ymax)
    size = struct.calcsize(EVENT_FMT)
    x = y = None

    while True:
        ready, _, _ = select.select([fd, sys.stdin], [], [])
        if fd in ready:
            data = os.read(fd, size * 64)
            for i in range(0, len(data) - size + 1, size):
                _, _, typ, code, val = struct.unpack(EVENT_FMT, data[i:i + size])
                if typ == EV_ABS and code == ABS_X:
                    x = (val - xmin) / float(xmax - xmin)
                elif typ == EV_ABS and code == ABS_Y:
                    y = (val - ymin) / float(ymax - ymin)
                elif typ == EV_KEY and code in (BTN_TOUCH, BTN_LEFT) and val == 0:
                    if x is not None and y is not None:
                        say("up", "%.6f" % x, "%.6f" % y)
        if sys.stdin in ready:
            line = sys.stdin.readline()
            if not line or line.startswith("cancel"):
                return
            cmd, _, arg = line.strip().partition(" ")
            try:
                if cmd == "write":
                    matrix, _, dev_name = arg.partition(" | ")
                    with open(UDEV_RULE, "w") as f:
                        f.write('# Written by elo-touch-fix\n'
                                'ACTION=="add|change", KERNEL=="event[0-9]*", '
                                'ATTRS{name}=="%s", '
                                'ENV{LIBINPUT_CALIBRATION_MATRIX}="%s"\n'
                                % (dev_name.replace('"', '?'), matrix))
                elif cmd == "remove" and os.path.exists(UDEV_RULE):
                    os.remove(UDEV_RULE)
                subprocess.call(["udevadm", "control", "--reload-rules"])
                os.close(fd)
                say("ok" if replug(path) else "replug-failed")
            except Exception as e:
                say("err", e)
            return


def replug(event_path):
    """Re-enumerate the USB device so the compositor re-reads udev
    properties. Returns False if it isn't a USB device."""
    import time
    p = os.path.realpath("/sys/class/input/%s/device" % os.path.basename(event_path))
    while p != "/":
        if os.path.exists(os.path.join(p, "authorized")) and \
           os.path.exists(os.path.join(p, "idVendor")):
            for v in ("0", "1"):
                with open(os.path.join(p, "authorized"), "w") as f:
                    f.write(v)
                time.sleep(1)
            return True
        p = os.path.dirname(p)
    subprocess.call(["udevadm", "trigger", "--action=change", "--subsystem-match=input"])
    return False


class Helper:
    """Starts the root helper via pkexec (one password prompt)."""

    def __init__(self, path):
        cmd = [sys.executable, os.path.abspath(__file__), "--helper", path]
        if os.geteuid() != 0:
            if not shutil.which("pkexec"):
                die("pkexec not found. Install it with: sudo apt install pkexec")
            cmd = ["pkexec"] + cmd
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1)
        first = self.p.stdout.readline().split()
        if not first or first[0] != "ready":
            die("could not open %s (password cancelled?)" % path)

    def command(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        reply = self.p.stdout.readline().strip()
        self.p.wait()
        if reply.startswith("err"):
            die("helper failed: " + reply)
        return reply


def collect_touches_evdev(h):
    """Like collect_touches(), but positions come from the raw device.
    Returns (raw, targets, w, h) with raw/targets normalised to 0..1."""
    try:
        import tkinter as tk
    except ImportError:
        die("python3-tk not installed. Install it with: sudo apt install python3-tk")

    root = tk.Tk()
    root.title("Elo touch calibration")
    root.attributes("-fullscreen", True)
    root.configure(background="black")
    root.update()
    w, hgt = root.winfo_width(), root.winfo_height()

    inset = 0.10
    targets = [(inset, inset), (1 - inset, inset), (1 - inset, 1 - inset),
               (inset, 1 - inset), (0.5, 0.5)]
    raw = []
    result = {"ok": False}
    canvas = tk.Canvas(root, background="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    def draw():
        canvas.delete("all")
        i = len(raw)
        canvas.create_text(w / 2, hgt * 0.30, fill="white", font=("Sans", 18),
                           justify="center",
                           text="Touch the centre of the red cross and lift your finger.\n"
                                "(Where the pointer goes doesn't matter.)\n"
                                "Esc = cancel     Point %d of %d" % (i + 1, len(targets)))
        tx, ty = targets[i][0] * w, targets[i][1] * hgt
        canvas.create_line(tx - 25, ty, tx + 25, ty, fill="red", width=3)
        canvas.create_line(tx, ty - 25, tx, ty + 25, fill="red", width=3)
        canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, outline="white", width=2)

    def on_helper(_fd, _mask):
        parts = h.p.stdout.readline().split()
        if not parts:
            root.destroy()
        elif parts[0] == "up":
            raw.append((float(parts[1]), float(parts[2])))
            if len(raw) == len(targets):
                result["ok"] = True
                root.destroy()
            else:
                draw()

    root.tk.createfilehandler(h.p.stdout, tk.READABLE, on_helper)
    root.bind("<Escape>", lambda _e: root.destroy())
    draw()
    root.focus_force()
    root.after(120000, root.destroy)
    root.mainloop()
    return (raw, targets, w, hgt) if result["ok"] else None


def udev_main(args):
    dev_name, path = find_event_device(args.device)
    print("device       : %s (%s)" % (dev_name, path))

    if args.action == "status":
        out = run("udevadm", "info", "--query=property", "--name=" + path).stdout
        m = re.search(r"^LIBINPUT_CALIBRATION_MATRIX=(.*)$", out, re.M)
        print("udev matrix  : " + (m.group(1) if m else "(none)"))
        return

    h = Helper(path)
    if args.action == "reset":
        reply = h.command("remove")
        print("calibration removed")
    else:
        if args.action == "flip":
            m, err = FLIP_Y, None
        else:
            res = collect_touches_evdev(h)
            if res is None:
                h.command("cancel")
                die("Calibration cancelled; nothing was changed.")
            raw, tgt, w, hgt = res
            try:
                m = fit_affine(raw, tgt)
            except ValueError as e:
                h.command("cancel")
                die("Calibration failed: %s. Please try again." % e)
            err = max((((apply_affine(m, *r)[0] - t[0]) * w) ** 2 +
                       ((apply_affine(m, *r)[1] - t[1]) * hgt) ** 2) ** 0.5
                      for r, t in zip(raw, tgt))
            print("matrix       : %s" % fmt(m))
            print("max error    : %.1f px" % err)
            if err > 0.05 * max(w, hgt):
                h.command("cancel")
                die("Touches were inconsistent (error %.0f px). Nothing was "
                    "changed - please run again and touch carefully." % err)
        reply = h.command("write %s | %s" % (fmt(m[:6]), dev_name))
        # An X11 per-user matrix would stack on top of this one; drop it.
        for p in (MATRIX_FILE, AUTOSTART):
            if os.path.exists(p):
                os.remove(p)
        msg = "Calibration saved to %s" % UDEV_RULE
        if err is not None:
            msg += " (accuracy +/- %.0f px)" % err
        print(msg)
        notify(msg + ".")

    if reply == "replug-failed":
        print("Unplug and re-plug the touchscreen USB cable (or reboot) to activate.")
        notify("Unplug and re-plug the touchscreen (or reboot) to activate.")


# --------------------------------------------------------------------------

def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--helper":
        return helper(sys.argv[2])

    ap = argparse.ArgumentParser(description="Calibrate an Elo AccuTouch touchscreen under X11.")
    ap.add_argument("action", nargs="?", default="calibrate",
                    choices=["calibrate", "apply", "flip", "reset", "status"])
    ap.add_argument("--device", help="exact xinput device name")
    ap.add_argument("--system", action="store_true",
                    help="also write %s (needs sudo)" % XORG_CONF)
    ap.add_argument("--udev", action="store_true",
                    help="use the Wayland/libinput method (automatic on Wayland)")
    args = ap.parse_args()

    if args.udev or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        if args.action == "apply":
            print("nothing to do: the udev rule applies automatically.")
            return
        return udev_main(args)

    if os.path.exists(UDEV_RULE) and args.action in ("calibrate", "flip"):
        die("A udev calibration (%s) is installed and would stack with this "
            "one. Re-run with --udev, or remove it with: "
            "elo-touch-fix reset --udev" % UDEV_RULE)

    check_session()
    dev = find_device(args.device)
    print("device       : " + dev)

    if args.action == "status":
        print("matrix       : " + fmt(get_matrix(dev)))
        li = get_matrix(dev, LIBINPUT_PROP)
        if li:
            print("libinput cal : " + fmt(li))
        if os.path.exists(MATRIX_FILE):
            print("saved        : " + open(MATRIX_FILE).read().strip())
        return

    if args.action == "apply":
        set_matrix(dev, load())
        return

    if args.action == "reset":
        set_matrix(dev, IDENTITY)
        for p in (MATRIX_FILE, AUTOSTART):
            if os.path.exists(p):
                os.remove(p)
        print("calibration removed (system file %s left untouched)" % XORG_CONF)
        return

    if args.action == "flip":
        m = FLIP_Y
        set_matrix(dev, m)
        save(dev, m)
        print("Y axis inverted. Run without arguments for a full calibration.")
    else:
        m, err = calibrate(dev)
        notify("Calibration saved (accuracy +/- %.0f px)." % err)

    if args.system:
        write_system_conf(dev, m)
    print("done - calibration is active and will be re-applied at login.")


if __name__ == "__main__":
    main()
