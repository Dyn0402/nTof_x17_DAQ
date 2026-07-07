# Keithley 2000 over GPIB — setup guide (for a Claude instance on a new machine)

This directory contains two scripts that read a **Keithley 2000 multimeter** over
**GPIB** using an **NI GPIB-USB-HS** adapter and the open-source **linux-gpib** stack:

- `keithley2000_read.py` — one-shot: `*IDN?` + a single measurement.
- `keithley2000_live.py` — live scrolling matplotlib plot, optional CSV logging.

This guide tells a fresh Claude instance how to reproduce the whole setup on
another modern-Ubuntu machine. It was first done on Ubuntu 24.04 / kernel 6.17
with an **NI GPIB-USB-HS (USB id `3923:709b`)**.

---

## Ground rules for the assistant

- **You cannot type a sudo password.** The `Bash` tool runs non-interactively, so
  `sudo` fails with "a terminal is required". For every root step, **hand the exact
  command to the user** and ask them to run it in the Claude Code prompt prefixed
  with `!` (e.g. `! sudo apt-get install ...`) so its output comes back to you.
- **Watch the working directory.** When you give the user a `cd ... && sudo make ...`
  line, include a `pwd` so you can confirm it ran in the intended directory — the
  user's shell keeps its own cwd between `!` commands. (We once installed the kernel
  package twice because the `cd` was dropped.)
- Do the non-root `make`/`configure`/`pip` steps yourself with `Bash`.

---

## Step 0 — Identify hardware and prerequisites

```bash
uname -r                                            # kernel version
ls -d /usr/src/linux-headers-$(uname -r) && echo HEADERS_OK   # need kernel headers
lsusb | grep -iE '3923|national'                    # NI vendor id = 3923; plug adapter in
```

- If headers are missing: `! sudo apt-get install -y linux-headers-$(uname -r)`.
- **NI GPIB-USB-HS (`3923:709b`) needs NO firmware upload.** Only the older USB-B
  and some HS+ variants need firmware; linux-gpib can't ship NI firmware, so if you
  ever hit an HS+/USB-B, grab it from https://github.com/fmhess/linux_gpib_firmware.

## Step 1 — Build dependencies (root)

Ask the user to run:

```bash
! sudo apt-get update -qq && sudo apt-get install -y \
    flex bison fxload autoconf automake libtool texinfo build-essential python3-dev
```

## Step 2 — Download linux-gpib

Version **4.3.7** is confirmed to build against kernel 6.17. It is a single tarball
containing separate kernel and user sub-tarballs.

```bash
mkdir -p gpib_build && cd gpib_build
url="https://downloads.sourceforge.net/project/linux-gpib/linux-gpib%20for%203.x.x%20and%202.6.x%20kernels/4.3.7/linux-gpib-4.3.7.tar.gz"
curl -fL --retry 3 -o linux-gpib-4.3.7.tar.gz "$url"
tar xzf linux-gpib-4.3.7.tar.gz
cd linux-gpib-4.3.7
tar xzf linux-gpib-kernel-4.3.7.tar.gz
tar xzf linux-gpib-user-4.3.7.tar.gz
```

(If a newer release exists, check the SourceForge listing; keep it recent enough to
support the running kernel. The whole `gpib_build/` tree is git-ignored.)

## Step 3 — Kernel modules

Build (non-root, you do this):

```bash
cd gpib_build/linux-gpib-4.3.7/linux-gpib-kernel-4.3.7
make -j4          # BTF "Skipping ... vmlinux" warnings are harmless
```

Install (root — hand to user; note the `cd` into the **-kernel-** dir):

```bash
! cd .../gpib_build/linux-gpib-4.3.7/linux-gpib-kernel-4.3.7 && pwd && \
    sudo rm -rf /lib/modules/$(uname -r)/gpib && sudo make install && sudo depmod -a && echo KERNEL_OK
```

## Step 4 — User-space library, tools, Python headers

Build (non-root, you do this):

```bash
cd gpib_build/linux-gpib-4.3.7/linux-gpib-user-4.3.7
./configure --sysconfdir=/etc
make -j4
```

Install (root — hand to user; note the `cd` into the **-user-** dir and confirm `pwd`):

```bash
! cd .../gpib_build/linux-gpib-4.3.7/linux-gpib-user-4.3.7 && pwd && \
    sudo make install && sudo ldconfig && echo USER_OK
```

This installs `libgpib.so` (`/usr/local/lib`), `gpib_config`/`ibtest`
(`/usr/local/sbin|bin`), the udev rules (`/etc/udev/rules.d/9*-*gpib*.rules`), and
the firmware auto-loader.

## Step 5 — Python binding into the target environment

The linux-gpib Python module is a C extension (`gpib` + `Gpib.py` wrapper). Install
it into whatever interpreter the project uses (here a venv at
`/home/dylan/PycharmProjects/nTof_x17_DAQ/.venv`).

> **Gotcha:** after `sudo make install`, the source tree's `build/` dir is owned by
> root, so `pip install .` fails with `Permission denied`. Copy the binding sources
> to a writable temp dir and install from there.

```bash
VENV=/path/to/.venv            # or: python3 -m venv .venv on the new machine
SRC=gpib_build/linux-gpib-4.3.7/linux-gpib-user-4.3.7/language/python
DST=$(mktemp -d)/gpib_pybind && mkdir -p "$DST"
cp "$SRC"/*.c "$SRC"/*.py "$SRC"/setup.py "$SRC"/README "$DST"/
"$VENV/bin/pip" install --no-build-isolation "$DST"
"$VENV/bin/python" -c "import gpib, Gpib; print('IMPORT OK')"
```

(For a system Python governed by PEP 668 you'd need `--break-system-packages`; a
venv avoids that. `matplotlib` must also be in the env for the live plot.)

## Step 6 — Configure the board (`/etc/gpib.conf`)

Create `/etc/gpib.conf` — board type for the GPIB-USB-HS is `ni_usb_b`:

```
interface { minor = 0; board_type = "ni_usb_b"; name = "ni_gpib"; pad = 0; master = yes; }
device    { minor = 0; name = "keithley2000"; pad = 16; }
```

Stage it with the `Write` tool then have the user copy it:
`! sudo cp <staged>/gpib.conf /etc/gpib.conf`

## Step 7 — Permissions for /dev/gpib0

The device comes up `root:root 0660`, so a non-root Python process can't open it.
Add a udev rule granting a group the user is already in (`plugdev` works; the stock
rule references a nonexistent `gpib` group). Hand to user:

```bash
! echo 'KERNEL=="gpib[0-9]*", GROUP="plugdev", MODE="0660"' | \
    sudo tee /etc/udev/rules.d/99-gpib-perms.rules && \
    sudo udevadm control --reload-rules && \
    sudo chgrp plugdev /dev/gpib0 2>/dev/null; sudo chmod 660 /dev/gpib0 2>/dev/null; echo PERMS_OK
```

(Confirm the user is in `plugdev` with `groups`; if not, pick another group they're in
or add them: `! sudo usermod -aG plugdev $USER` then re-login.)

## Step 8 — Bring the board up

```bash
! sudo modprobe ni_usb_gpib && sleep 1 && sudo gpib_config --minor 0 && echo CONFIG_OK && ls -l /dev/gpib0
```

**Persistence:** on reboot/replug this is automatic — the module auto-loads because
`3923p709B` is in `modules.alias`, and a udev rule runs `gpib_config`. Verify with:
`grep 3923p709B /lib/modules/$(uname -r)/modules.alias`.
After a **kernel upgrade**, re-run Step 3 (rebuild + install the kernel module).

## Step 9 — Find the instrument's GPIB address, then test

**The Keithley's address is NOT guaranteed to be 16.** On the first machine it was
**15**. Scan the bus:

```bash
"$VENV/bin/python" - <<'PY'
import gpib
print("listeners:", [p for p in range(1,31) if gpib.listener(0, p)])
PY
```

Then read (substitute the pad you found):

```bash
"$VENV/bin/python" keithley2000_read.py --pad <PAD>
# -> *IDN? -> KEITHLEY INSTRUMENTS INC.,MODEL 2000,...
```

Update the `--pad` default in both scripts to the discovered address if you like.

## Step 10 — Live plot

```bash
MPLBACKEND=TkAgg "$VENV/bin/python" keithley2000_live.py --pad <PAD>
```

- **Force `MPLBACKEND=TkAgg`** — the Qt backend is often not installed and errors out;
  Tk ships with the `python3-tk`/tkinter stack. Install if missing:
  `! sudo apt-get install -y python3-tk`.
- Options: `--func {VOLT:DC,VOLT:AC,CURR:DC,RES,FRES,FREQ,TEMP}`, `--nplc 0.1`
  (faster/noisier), `--interval 100` (ms), `--window 120` (s of history),
  `--csv out.csv` (log while plotting). Keys: `q` quit, `c` clear.
- Headless self-test (no GUI needed) to prove the whole chain works:
  `"$VENV/bin/python" keithley2000_live.py --pad <PAD> --duration 6 --save /tmp/test.png`

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `write() failed: ... no listeners currently addressed` | Wrong GPIB address — rescan (Step 9); check the GPIB cable and that the DMM is on. |
| `could not create 'build/.../wheel': Permission denied` | Root-owned `build/` from `sudo make`; copy binding sources out first (Step 5). |
| `ModuleNotFoundError: No module named 'Gpib'` | Binding not installed into *this* interpreter; re-run Step 5 against the right venv. |
| Permission denied opening `/dev/gpib0` | Perms/udev not applied (Step 7), or user not in `plugdev`. |
| Qt / "no Qt backend" error from matplotlib | Use `MPLBACKEND=TkAgg` (Step 10). |
| Modules fail to build after OS update | Kernel changed; re-run Step 3. |
