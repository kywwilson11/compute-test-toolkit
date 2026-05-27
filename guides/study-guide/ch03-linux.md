## Linux for Bring-up and Hardware Debug

The compute boards you test are running Linux. The test station running the test is running Linux. The virtual filesystems `/sys`, `/proc`, and `dmesg` are where the kernel exposes its live view of every device, driver, interrupt, and error counter -- all as plain files you read or write with ordinary shell tools. This chapter covers the parts of Linux a Compute Test Engineer lives in daily: hardware enumeration, device-tree debugging, driver management, sysfs-based hardware interrogation, and recovering a wedged board.

Everything in Linux is a file. A PCIe link width is a file. An Advanced Error Reporting (AER) error counter is a file. A thermal zone temperature is a file. That abstraction is exactly why the command-line toolkit is so powerful: querying hardware state is `cat /sys/...`, and scripting it is `read_text()` in Python.

---

## Filesystem Hierarchy Reference

| Path | What lives there |
|---|---|
| `/` | Root of the whole tree |
| `/bin`, `/usr/bin` | Essential user binaries (`ls`, `cp`, `grep`) -- often merged in modern distros |
| `/sbin`, `/usr/sbin` | System binaries (`fdisk`, `modprobe`, `ip`, `reboot`) |
| `/etc` | System configuration (`/etc/fstab`, `/etc/passwd`, `/etc/systemd/`) |
| `/home/<user>` | Per-user home directories |
| `/var/log` | System and service logs -- CHECK HERE FIRST when debugging |
| `/run`, `/var/run` | Runtime data (PID files, sockets); a tmpfs cleared on boot |
| `/proc` | Virtual FS: kernel + per-process info, generated on read, not on disk |
| `/sys` | Virtual FS: device/driver tree; hardware attributes you read and write |
| `/dev` | Device files (`/dev/nvme0n1`, `/dev/ttyUSB0`, `/dev/null`) |
| `/tmp` | Temporary files (often cleared on reboot) |
| `/opt` | Optional/third-party software; a common home for `/opt/atf`, `/opt/test` |
| `/mnt`, `/media` | Mount points for removable media |
| `/boot` | Kernel image, initramfs, GRUB config |
| `/lib`, `/usr/lib` | Shared libraries and kernel modules |

---

## Device Enumeration

### PCIe: lspci

`lspci` is the daily driver. Understand its flags deeply because every PCIe debug session starts here.

```bash
lspci                          # one-line summary per device
lspci -nn                      # append numeric [vendor:device] IDs (essential for grep and scripts)
lspci -D                       # show the full DOMAIN in the BDF (0000:03:00.0, not just 03:00.0)
lspci -vvv                     # MAXIMUM verbosity: capabilities, LnkCap/LnkSta, AER, ASPM, MSI
lspci -k                       # show kernel driver and modules bound to each device
lspci -t                       # TREE view of bus topology (parent ports -> endpoints)
lspci -d 10de:                 # filter by VENDOR ID (10de = NVIDIA)
lspci -d ::0302                # filter by CLASS code (0302 = 3D controller)
lspci -d ::0200                # CLASS 0200 = Ethernet
lspci -d ::0108                # CLASS 0108 = NVMe
lspci -s 0000:03:00.0 -vvv     # full detail on ONE specific device by BDF
lspci -s 03:00.0 -vvv | grep -i 'lnkcap\|lnksta'   # capable vs. actual link, side by side
```

Representative `lspci -s 03:00.0 -vvv` fragment showing a healthy PCIe Gen4 x16 link:

```text
03:00.0 3D controller: NVIDIA Corporation Device 2342 (rev a1)
...
        LnkCap: Port #0, Speed 16GT/s, Width x16, ASPM L0s L1, ...
        LnkSta: Speed 16GT/s (ok), Width x16 (ok)
...
        Capabilities: [100 v2] Advanced Error Reporting
                UESta:  DLP- SDES- TLP- FCP- CmpltTO- CmpltAbrt- ...
                CESta:  RxErr- BadTLP- BadDLLP- Rollover- Timeout- ...
```

If `LnkSta` shows `Speed 8GT/s` when `LnkCap` says `16GT/s`, or `Width x8` when capable of `x16`, the link degraded. That is always abnormal and requires investigation.

Degraded-link failure signatures and initial triage:

```text
LnkCap: Speed 16GT/s, Width x16  /  LnkSta: Speed 8GT/s (downgraded), Width x16 (ok)
  -> Speed degraded only. Common cause: endpoint or root port could not complete
     equalization at Gen4. Check BIOS PCIe Gen setting, retimer firmware, and dmesg
     for "dpc" (Downstream Port Containment) or equalization errors at boot.

LnkCap: Speed 16GT/s, Width x16  /  LnkSta: Speed 16GT/s (ok), Width x8 (downgraded)
  -> Width degraded only. One or more lanes failed during link training. Check for
     physical damage, bent connector pins, or an open-circuit trace.

LnkCap: Speed 16GT/s, Width x16  /  LnkSta: Speed 2.5GT/s (downgraded), Width x1 (downgraded)
  -> Worst case: trained to Gen1 x1. Device is present but barely functional.
     Almost always a physical problem: wrong slot, bad connector, or broken retimer.

lspci -vvv also shows ASPM state (L0s, L1) and whether ASPM is enabled.
  -> If ASPM L1 is active and you're seeing ReplayTimeout AER events, try disabling
     ASPM as a diagnostic step: setpci -s 03:00.0 CAP_EXP+0x10.W=0x0000
     (writes 0 to LnkCtl register -- clears ASPM bits; confirm with lspci -vvv after).
```

#### setpci: Direct Config Space Access

```bash
# setpci reads and writes PCI config space registers directly.
# Sizes: .B = byte, .W = word (2B), .L = long (4B).
setpci -s 03:00.0 COMMAND.W             # read the Command register (offset 0x04)
setpci -s 03:00.0 04.W                  # same by hex offset
setpci -s 03:00.0 COMMAND.W=0x0146     # WRITE -- use with care; wrong writes brick a device

# Common registers (use: setpci --dumpregs for the full list of named registers):
# 0x00.L = vendor/device ID
# 0x04.W = command (bus master, mem space, I/O space enable)
# 0x06.W = status (signaled target abort, master abort, etc.)
# 0x08.B = revision ID
# 0x3D.B = interrupt pin
# Use setpci when you need to poke a register that sysfs does not expose directly.
# Extended capabilities (AER, ASPM, SR-IOV) live at offset >= 0x100 and are best
# reached via: setpci -s 03:00.0 CAP_EXP+<offset>.<width>
```

### sysfs: /sys/bus/pci

sysfs is the scripting goldmine. Everything `lspci` shows is also available as individual files -- readable directly from Bash or Python without spawning a subprocess.

```bash
ls /sys/bus/pci/devices/            # every PCIe function, named by BDF

BDF=0000:03:00.0
SYS=/sys/bus/pci/devices/$BDF

cat $SYS/vendor                     # 0x10de  (NVIDIA)
cat $SYS/device                     # numeric device ID
cat $SYS/class                      # 0x030000 = VGA/display, 0x010802 = NVMe, 0x020000 = Ethernet
cat $SYS/revision                   # silicon revision
cat $SYS/current_link_speed         # "16 GT/s" -- the speed this link actually trained to
cat $SYS/current_link_width         # "16"      -- lane width actually negotiated
cat $SYS/max_link_speed             # "16 GT/s" -- maximum the device is capable of
cat $SYS/max_link_width             # "16"
cat $SYS/numa_node                  # which NUMA node this device is attached to
cat $SYS/irq                        # legacy IRQ line
cat $SYS/enable                     # 1 if device is enabled
readlink $SYS/driver                # symlink -> bound driver (e.g. .../drivers/nvme)
xxd $SYS/config | head              # RAW PCI config space (vendor/device, command/status, BARs)

# Degradation check in one line:
[[ "$(cat $SYS/current_link_speed)" == "$(cat $SYS/max_link_speed)" ]] || echo "speed degraded"

# Kernel-tracked AER error counters (Linux 4.19+, requires CONFIG_PCIEAER):
cat $SYS/aer_dev_correctable        # RxErr, BadTLP, BadDLLP, Rollover, Replay Timer, NonFatalErr
cat $SYS/aer_dev_nonfatal
cat $SYS/aer_dev_fatal
# These are the right way to track AER on a production station -- no setpci required.
# All entries are "name <tab> count" -- zero counts are still printed.
# TOTAL_ERR_COR at the end may not equal the sum of individual counts
# (multiple errors can arrive in a single ERR_COR message).

# Administrative actions (use deliberately -- these perturb a live link):
echo 1 | sudo tee $SYS/remove           # hot-remove the device from the kernel
echo 1 | sudo tee /sys/bus/pci/rescan   # re-scan the bus to rediscover it
echo 1 | sudo tee $SYS/reset            # function-level reset (FLR), if the device supports it
```

Enumerate all degraded links in one loop:

```bash
for d in /sys/bus/pci/devices/*/; do
    cs=$(cat "$d/current_link_speed" 2>/dev/null) || continue
    ms=$(cat "$d/max_link_speed"     2>/dev/null) || continue
    cw=$(cat "$d/current_link_width" 2>/dev/null)
    mw=$(cat "$d/max_link_width"     2>/dev/null)
    [[ "$cs" == "$ms" && "$cw" == "$mw" ]] && continue
    echo "DEGRADED ${d##*devices/}  speed ${cs}/${ms}  width x${cw}/x${mw}"
done
```

Python is preferred for production test code -- `pathlib` reads these files directly with zero parsing fragility:

```python
from pathlib import Path

def enumerate_pcie() -> list[dict]:
    """Read every PCIe device from sysfs -- no subprocess, no text parsing."""
    devices = []
    for dev in sorted(Path("/sys/bus/pci/devices").iterdir()):
        def attr(name: str) -> str | None:
            try:
                return (dev / name).read_text().strip()
            except (FileNotFoundError, PermissionError):
                return None
        speed, max_speed = attr("current_link_speed"), attr("max_link_speed")
        width, max_width = attr("current_link_width"), attr("max_link_width")
        devices.append({
            "bdf": dev.name,
            "vendor": attr("vendor"),
            "device": attr("device"),
            "speed": speed,
            "width": f"x{width}" if width else None,
            "max_speed": max_speed,
            "max_width": f"x{max_width}" if max_width else None,
            "degraded": bool(max_speed) and (speed != max_speed or width != max_width),
        })
    return devices

def degraded_links() -> list[dict]:
    return [d for d in enumerate_pcie() if d["degraded"]]
```

### USB, Block, CPU, and Full Inventory

```bash
# USB
lsusb                              # summary list
lsusb -v                           # verbose (includes VID/PID, class, endpoints)
lsusb -t                           # topology TREE (shows hub hierarchy)

# Block devices / storage
lsblk                              # tree of block devices, partitions, mountpoints
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL
nvme list                          # NVMe namespaces with model/serial/size
nvme list -o json | jq '.'         # JSON form (for scripting)

# CPU
lscpu                              # architecture, sockets/cores/threads, cache sizes, flags
nproc                              # number of online logical CPUs (use this in scripts)
cat /proc/cpuinfo | grep 'model name' | head -1   # CPU model string

# Full hardware inventory (lshw is called out in the Zoox JD)
lshw                               # complete hardware tree (very detailed)
lshw -short                        # one-line-per-device summary table
lshw -json                         # JSON output -- parse this in Python, not the text form
lshw -class network                # only NICs
lshw -class storage                # only storage controllers
lshw -class display                # only GPUs
lshw -class disk

# DIMM slot inventory
dmidecode --type memory            # slot info: type, speed, size, manufacturer, part number
dmidecode --type bios              # BIOS vendor/version/release date
dmidecode --type system            # manufacturer, product name, serial, UUID
dmidecode --type processor         # CPU socket details
```

---

## dmesg and journald: Kernel Messages

`dmesg` is usually the **first** place to look when a board does something unexpected. Hardware faults, driver bind/unbind, PCIe AER events, Non-Volatile Memory Express (NVMe) controller resets, thermal throttling, and OOM kills all land here.

### dmesg

```bash
dmesg                              # full ring buffer
dmesg -T                           # human-readable wall-clock timestamps (not seconds since boot)
dmesg -w                           # FOLLOW live (like tail -f) -- watch events as they happen
dmesg -l err,warn                  # only error and warning level messages
dmesg | tail -50                   # most recent messages
dmesg -T | grep -i 'pcie\|aer'     # PCIe / AER events
dmesg -T | grep -i 'nvme'          # NVMe resets, timeouts, controller errors
dmesg -T | grep -i 'thermal\|throttl'   # thermal throttling events
dmesg -T | grep -iE 'error|fail|fault|timeout|reset'   # broad hardware-trouble sweep
sudo dmesg -C                      # CLEAR the ring buffer (arm before a stress run, re-read after)
journalctl -k -b                   # same kernel messages, persisted across reboots (this boot)
journalctl -k -b -1                # previous boot's kernel messages
```

### Decoding Common dmesg Signatures

```text
PCIe AER correctable error (recoverable, but non-zero = investigate):
[  42.185] pcieport 0000:00:01.0: AER: Corrected error received: id=0108
[  42.185] 0000:03:00.0: PCIe Bus Error: severity=Corrected, type=Physical Layer,
           id=0300(Receiver ID)
[  42.185] 0000:03:00.0:   device [10de:2342] error status/mask=00000001/00002000
[  42.185] 0000:03:00.0:    [0] RxErr

-> BadTLP / BadDLLP / RxErr: physical layer noise (marginal lanes, power integrity).
   If the count is static and never rises again, it may be a boot-transient.
   If the count climbs under GPU/NVMe load, it is an SI / signal integrity issue.

-> ReplayTimeout: retrain happening; link is marginal under traffic.
   Cross-check: cat current_link_speed -- if it dropped to 8GT/s from 16GT/s, the
   link down-trained due to too many errors.

-> Uncorrectable Fatal (UESta: MalformedTLP+ or SurpriseDown+):
   The endpoint reset. Expect "device disabled" or driver unbind messages to follow.
   Immediately do: lspci -s $BDF && cat aer_dev_fatal to get the full picture.

PCIe AER uncorrectable non-fatal:
[  55.300] 0000:03:00.0: PCIe Bus Error: severity=Uncorrected (Non-Fatal), type=Transaction Layer
[  55.300] 0000:03:00.0:   device [10de:2342] error status/mask=00000020/00000000
[  55.300] 0000:03:00.0:    [5] SDES          (First)
-> SDES = Symbol and Disparity Error. Physical layer symbol error.
   Action: check retimer firmware; reseat connector; rule out power noise.

NVMe timeout / reset:
[  88.002] nvme nvme0: I/O 23 QID 1 timeout, disable controller
[  88.002] nvme nvme0: Device shutdown timeout. Resetting the device.
-> Power delivery to the M.2 slot, PCIe lane integrity, or firmware bug on NVMe side.
   Follow-up: nvme smart-log /dev/nvme0 to check media_errors and critical_warning.
   If media_errors > 0 on a brand-new drive, it is a bad unit.

NVMe reset after link retrain:
[  89.010] nvme nvme0: controller is down; will reset: CSTS=0x3, PCI_STATUS=0x10
-> CSTS fatal status + PCI_STATUS bit 4 (Master Data Parity Error). The NVMe lost
   its PCIe link before completing an I/O. Root cause is usually in the PCIe lane,
   not the NVMe itself.

Thermal throttling:
[ 312.001] CPU0: Package temperature above threshold, cpu clock throttled
-> Normal if the board is hot; abnormal at idle or if the thermal solution is misapplied.
   Follow-up: sensors; cat /sys/class/thermal/thermal_zone*/temp; check fan RPM.

Driver bind failure:
[   5.223] nvidia: probe of 0000:03:00.0 failed with error -12
-> Error -12 = ENOMEM. Driver could not allocate memory.
   Too many GPUs for available system RAM, or IOMMU aperture exhausted.
   Other common errors: -22 = EINVAL (bad parameter); -5 = EIO (hardware not responding).

OOM kill:
[1234.5] Out of memory: Kill process 4321 (pytest) score 847 or sacrifice child
-> Station is memory-constrained under load. free -h to check swap usage.
   If swap is full too, either add RAM or reduce test parallelism.

MCE (Machine Check Exception):
[  10.555] mce: [Hardware Error]: CPU 0: Machine Check: 0 Bank 1: b200000000000800
[  10.555] mce: [Hardware Error]: TSC 0 ADDR 0xffff8800deadbeef MISC 0x68
-> Corrected MCE: the hardware fixed it; monitor count with 'mcelog' or 'rasdaemon'.
-> Uncorrected MCE (MCACOD in bank): CPU internal fault, ECC memory error, or
   interconnect fault. An uncorrected MCE usually triggers a kernel panic.
   Action: check dimm slot with dmidecode; run memtest86+.
```

### journalctl

`journalctl` extends `dmesg` -- it persists across reboots and covers all systemd unit output:

```bash
journalctl -u atf-dashboard                       # all logs for one service
journalctl -u atf-dashboard -f                    # FOLLOW live (like tail -f)
journalctl -u atf-dashboard -n 100 --no-pager     # last 100 lines without a pager
journalctl -u atf-dashboard --since '1 hour ago'  # time window ("yesterday", "2 hours ago")
journalctl -u atf-dashboard -p err                # priority err and above (emerg..err)
journalctl -k                                     # kernel messages only (persisted dmesg)
journalctl -b                                     # since the current boot
journalctl -b -1                                  # PREVIOUS boot (debug a crash/reboot)
journalctl --since "2026-04-01" --until "2026-04-02"
journalctl --disk-usage                           # how much space the journal uses
sudo journalctl --vacuum-time=7d                  # trim to the last 7 days
```

---

## udev Rules

udev runs in userspace and handles everything that happens when the kernel detects a new device: creating `/dev` nodes, setting permissions, running scripts, loading firmware. When a device node appears at an unpredictable name (`/dev/ttyUSB0` on one boot, `/dev/ttyUSB1` on another) or with the wrong permissions, udev rules fix it.

```bash
# Inspect existing rules:
ls /lib/udev/rules.d/               # system rules (from packages -- read but do not edit)
ls /etc/udev/rules.d/               # local overrides (your rules go here)

# Watch udev events in real time (plug in a device while this runs):
udevadm monitor --environment --udev

# Query a device's properties (everything available for rule matching):
udevadm info -a -n /dev/ttyUSB0     # -a walks up the hierarchy (parent chain)
udevadm info -a -n /dev/nvme0       # NVMe
udevadm info /sys/bus/pci/devices/0000:03:00.0   # PCIe device by sysfs path
```

A rule that gives every FTDI serial adapter (VID 0403) a stable symlink and allows the `testeng` group to access it without sudo:

```text
# /etc/udev/rules.d/99-test-instruments.rules
# Assign a stable symlink and set group permissions for all FTDI adapters.
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", \
    SYMLINK+="ttyFTDI%n", GROUP="testeng", MODE="0664"

# A specific device by serial number (use: udevadm info -a to find ATTRS{serial}):
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{serial}=="A50285BI", \
    SYMLINK+="psu_console", GROUP="testeng", MODE="0664"

# NVMe device: set group ownership so testeng user can run nvme commands without sudo.
SUBSYSTEM=="nvme", KERNEL=="nvme[0-9]*", GROUP="testeng", MODE="0660"
```

```bash
# Reload rules and re-trigger without rebooting:
sudo udevadm control --reload-rules
sudo udevadm trigger

# Test a rule (dry run -- shows what WOULD happen):
udevadm test /sys/bus/usb/devices/1-1.2/1-1.2:1.0   # use the path from udevadm monitor
```

Rule file naming: files are processed in lexical order. Use `99-` prefix for your rules so they run after distro rules (which set defaults you may want to override).

---

## Kernel Modules

Drivers live as loadable kernel modules. When a device does not appear or behaves incorrectly, the module is the first thing to inspect.

```bash
lsmod                           # all currently loaded modules + use counts + dependents
lsmod | grep nvme               # is the nvme driver loaded?
modinfo nvme                    # metadata: file path, parameters, dependencies, version
modinfo -p nvme                 # just the tunable parameters
sudo modprobe nvme              # load a module (and its dependencies)
sudo modprobe -r nvme           # unload (fails if in use: use count > 0)
sudo modprobe nvme io_timeout=30   # load with a parameter
sudo rmmod nvme                 # low-level remove (no dep handling -- prefer modprobe -r)
dmesg | grep -i nvme            # kernel messages as the driver loaded, bound, or errored
```

Persisting module configuration:

```bash
# Blacklist a module (e.g. prevent the open-source nouveau from loading so nvidia can bind):
echo "blacklist nouveau" | sudo tee /etc/modprobe.d/blacklist-nouveau.conf

# Set a parameter at load time:
echo "options nvme io_timeout=30" | sudo tee /etc/modprobe.d/nvme.conf

# Force-load a module at boot:
echo "nvme" | sudo tee /etc/modules-load.d/nvme.conf

# After any modprobe.d change, rebuild the initramfs so the change takes effect early:
sudo update-initramfs -u        # Debian/Ubuntu
sudo dracut -f                  # RHEL/Fedora
```

### DKMS: Driver Modules Across Kernel Updates

Out-of-tree drivers (proprietary GPU, custom hardware) break when the kernel upgrades unless they are managed by DKMS (Dynamic Kernel Module Support), which rebuilds them automatically:

```bash
dkms status                     # show all DKMS-managed modules and their build status
sudo dkms install nvidia/535.86.10   # manually trigger a build
sudo dkms remove  nvidia/535.86.10 --all   # remove

# If dkms autoinstall fails after a kernel upgrade:
sudo dkms autoinstall           # rebuild all registered modules for the running kernel
# Check /var/lib/dkms/<name>/<version>/build/make.log for compiler errors
```

---

## PCI Config Space and AER

The 256-byte (or 4096-byte extended) PCI configuration space holds device identity, capabilities, command/status, and -- in the extended capabilities -- AER, Active State Power Management (ASPM), Single Root I/O Virtualization (SR-IOV). Understanding it is necessary for low-level debug.

```bash
# Read raw config space:
xxd /sys/bus/pci/devices/0000:03:00.0/config | head -20
#   Bytes 0-1:  Vendor ID
#   Bytes 2-3:  Device ID
#   Bytes 4-5:  Command register (bus master, mem/IO space enable)
#   Bytes 6-7:  Status register
#   Byte 8:     Revision ID
#   Byte 9-11:  Class code
#   Bytes 16-39: Base Address Registers (BARs 0-5)
#   Byte 52-55: Subsystem Vendor/Device IDs
#   Byte 60:    Interrupt line
#   Byte 61:    Interrupt pin

# setpci: read/write specific registers
setpci -s 03:00.0 COMMAND.W       # read Command register
setpci -s 03:00.0 04.W            # same by hex offset

# AER registers live in the Extended Capability block (PCIe extended config space, offset >= 0x100).
# The kernel-tracked counters in sysfs are the preferred interface:
cat /sys/bus/pci/devices/0000:03:00.0/aer_dev_correctable
```

AER correctable error register bits (from the PCIe spec):

| Bit | Name | What it means at the bench |
|---|---|---|
| 0 | RxErr | Receiver error; physical layer noise on the lane |
| 6 | BadTLP | Bad TLP received; framing or data corruption |
| 7 | BadDLLP | Bad DLLP; data-link layer issue |
| 8 | Rollover | Error counter rolled over (not a real error event) |
| 12 | ReplayTimeout | Replay timer expired; link retrain under traffic |
| 13 | NonFatalErr | Advisory non-fatal; escalated from COR |

Real counter file output -- `cat /sys/bus/pci/devices/0000:03:00.0/aer_dev_correctable`:

```text
# CLEAN board (all zeros -- the only acceptable result at station):
Receiver Error            0
Bad TLP                   0
Bad DLLP                  0
RELAY_NUM Rollover        0
Replay Timer Timeout      0
Advisory Non-Fatal        0
Corrected Internal Error  0
Header Log Overflow       0
TOTAL_ERR_COR             0

# MARGINAL board (BadTLP climbing under GPU workload):
Receiver Error            0
Bad TLP                  47       <- non-zero: physical layer / SI issue
Bad DLLP                  0
RELAY_NUM Rollover        0
Replay Timer Timeout     12       <- retrain is happening under load
Advisory Non-Fatal        0
TOTAL_ERR_COR            59
```

Failure signatures and what to do next:

```text
Growing RxErr or BadTLP under load
  -> Lane marginality: SI problem, marginal trace, bad connector, failed retimer.
     Action: check lspci LnkSta for width downgrade; run eye-diagram test; inspect
     board layout / connector seating.

Growing Replay Timer Timeout (without RxErr/BadTLP)
  -> Link retrain at speed; can be ASPM-related or thermal drift on the retimer.
     Action: check current_link_speed; disable ASPM and retest (setpci LNKCTL.W).

Any Fatal counter (UESta: MalformedTLP+ or SurpriseDown+)
  -> The endpoint was reset. The driver may have unbound.
     Action: check dmesg for "AER: broadcast error_detected" or "device disabled";
     check lspci for device presence; trigger FLR if driver is still bound.

UESta: Surprise Down
  -> Device disappeared from the bus mid-operation.
     Action: check power delivery to the slot; check PCIe reset (PERST#) signal.
```

PCIe link recovery flow (device appears but is degraded or re-enumerating):

```bash
BDF=0000:03:00.0
SYS=/sys/bus/pci/devices/$BDF

# 1. Confirm the device is present:
lspci -s "$BDF" -vvv | head -5

# 2. Check current vs maximum link:
echo "Speed:  $(cat $SYS/current_link_speed) / $(cat $SYS/max_link_speed)"
echo "Width:  x$(cat $SYS/current_link_width) / x$(cat $SYS/max_link_width)"

# 3. Attempt a function-level reset (FLR) -- if the device supports it:
echo 1 | sudo tee $SYS/reset

# 4. If FLR does not recover it, hot-remove and rescan:
echo 1 | sudo tee $SYS/remove
sleep 1
echo 1 | sudo tee /sys/bus/pci/rescan

# 5. If still wrong after rescan, pull AER state:
cat $SYS/aer_dev_correctable
cat $SYS/aer_dev_fatal
dmesg -T | grep -i "$BDF"

# Note: remove+rescan does not power-cycle the device; it only removes/re-adds the
# kernel's representation. A true power-cycle requires PERST# or BMC control.
```

---

## Interrupts and IRQ Affinity

```bash
cat /proc/interrupts            # interrupt counts per IRQ, broken out PER CPU
# Columns: IRQ#, one count column per CPU, then the controller type and device/driver name.
# Uses:
#   - Is the device's interrupt firing at all? (zero count under load = wrong/dead IRQ)
#   - Are interrupts spread across CPUs or piled on CPU0? (CPU0 pile-up caps throughput)
#   - MSI/MSI-X vectors appear by name: confirm an NVMe or NIC negotiated multiple queues.

cat /proc/softirqs              # software interrupts (NET_RX/NET_TX for NIC throughput)

# Watch NVMe IRQs increment live under fio load:
watch -n1 'cat /proc/interrupts | grep -i nvme'

# IRQ affinity: which CPUs service a given IRQ
cat /proc/irq/<NUM>/smp_affinity      # bitmask (hex)
cat /proc/irq/<NUM>/smp_affinity_list # human-readable CPU list (e.g. "0-3")

# Set affinity (route IRQ 42 to CPUs 2 and 3):
echo "c" | sudo tee /proc/irq/42/smp_affinity   # 0xc = binary 1100 = CPUs 2,3

# irqbalance daemon automatically spreads IRQs across CPUs.
# On a test station running latency-sensitive workloads you may want to stop it:
sudo systemctl stop irqbalance
sudo systemctl disable irqbalance
# Then set affinity manually to pin NIC/NVMe interrupts to the CPUs that run the test.
```

---

## i2c-tools

I2C is ubiquitous on compute boards for power management ICs, temperature sensors, EEPROMs, fan controllers, and voltage regulators. `i2c-tools` gives you direct bus access from userspace.

```bash
# Enumerate I2C adapters:
ls /dev/i2c-*                         # typically /dev/i2c-0 through /dev/i2c-N
i2cdetect -l                          # list adapters with their names and parent (from device tree)

# Scan for devices on a bus (bus number = the N in /dev/i2c-N):
i2cdetect -y 0                        # scan bus 0 (-y skips interactive prompt; -r uses receive-byte probe instead of quick-write)
# Output: a 7-bit address grid. -- = no device. UU = in use by a kernel driver. 0x68 = present.

# Read registers:
i2cget -y 0 0x48 0x00 b               # bus 0, address 0x48, register 0x00, byte read
i2cget -y 0 0x48 0x00 w               # word (16-bit) read

# Write a register:
i2cset -y 0 0x48 0x01 0x60 b          # write 0x60 to register 0x01 on address 0x48

# Dump all readable registers of a device:
i2cdump -y 0 0x48 b                   # bus 0, address 0x48, byte mode

# Read a raw register without specifying a register (useful for some sensors):
i2cget -y 0 0x48                      # returns whatever the device sends next
```

Practical use cases on a compute board:

```bash
# Read a TMP102 temperature sensor at address 0x48 on I2C bus 0.
# TMP102 register 0x00 is a 16-bit two's complement value, MSB first; 0.0625 C/LSB:
raw=$(i2cget -y 0 0x48 0x00 w)
# raw is big-endian: swap bytes and shift
temp_raw=$(( ( (0x${raw:2:2}) | ((0x${raw:4:2}) << 8) ) >> 4 ))
echo "Temperature: $(awk "BEGIN{printf \"%.2f\", $temp_raw * 0.0625}") C"

# Read a PMBus power rail voltage (device at 0x40, VOUT_MODE + VOUT_COMMAND registers):
i2cget -y 1 0x40 0x20 b   # VOUT_MODE
i2cget -y 1 0x40 0x21 w   # VOUT_COMMAND (raw voltage word -- decode per PMBus spec)
```

The `i2c-dev` kernel module must be loaded for `/dev/i2c-*` to exist:

```bash
sudo modprobe i2c-dev
echo "i2c-dev" | sudo tee /etc/modules-load.d/i2c-dev.conf   # persist across reboots
```

---

## MTD / Flash

Many compute boards have NOR or NAND flash for firmware (UEFI/BIOS, BMC, Field-Programmable Gate Array (FPGA) images). Linux exposes these via the MTD (Memory Technology Device) subsystem.

```bash
cat /proc/mtd                    # MTD partitions present (name, size, erase size)
# Example output:
# dev:    size   erasesize  name
# mtd0: 00200000 00001000 "bios"
# mtd1: 00800000 00001000 "bmc"

ls /dev/mtd*                     # mtdN = raw partition, mtdblockN = block device interface

# Read a partition to a file (firmware backup before update):
sudo dd if=/dev/mtd0 of=bios_backup.bin bs=4096   # or use nanddump for NAND

# Tools from mtd-utils:
sudo apt install mtd-utils

flashcp -v new_bios.bin /dev/mtd0    # program a NOR flash partition (verify after)
nandwrite -p /dev/mtd1 new_bmc.bin   # write NAND (without bad-block skip: add -k)
nanddump -f saved.bin /dev/mtd1      # read NAND to a file (skips bad blocks by default)

mtdinfo /dev/mtd0                    # geometry: size, erase size, bad-block handling
flash_eraseall /dev/mtd0             # erase entire partition (DESTRUCTIVE)
flash_eraseall -j /dev/mtd0          # erase and format as JFFS2

# Verify a flash region matches a known-good image:
sudo dd if=/dev/mtd0 bs=4096 | md5sum
md5sum reference.bin
# A mismatch after flashing = incomplete write or read-back error.
```

---

## Power and Thermal

### hwmon: Hardware Monitor via sysfs

```bash
ls /sys/class/hwmon/              # hwmon0, hwmon1, ... each is a sensor chip
cat /sys/class/hwmon/hwmon0/name  # chip name (e.g. "coretemp", "nct6779", "ina3221")

# Temperature sensors (in_tempN_*):
cat /sys/class/hwmon/hwmon0/temp1_input   # temperature in millidegrees C (e.g. 45000 = 45 C)
cat /sys/class/hwmon/hwmon0/temp1_max     # configured max (millidegrees)
cat /sys/class/hwmon/hwmon0/temp1_crit    # critical threshold
cat /sys/class/hwmon/hwmon0/temp1_label   # what is being measured (e.g. "Package id 0")

# Fan speed:
cat /sys/class/hwmon/hwmon0/fan1_input    # RPM

# Voltage / current (INA3221, INA219, etc.):
cat /sys/class/hwmon/hwmon0/in1_input     # millivolts
cat /sys/class/hwmon/hwmon0/curr1_input   # milliamps

# Print all labeled temperatures in human-readable form:
paste <(cat /sys/class/thermal/thermal_zone*/type) \
      <(cat /sys/class/thermal/thermal_zone*/temp) | \
awk '{printf "%-22s %.1f C\n", $1, $2/1000}'

# sensors command (lm-sensors package) aggregates hwmon for a human view:
sensors                           # all chips
sensors -u                        # raw values (what sysfs actually contains)
sensors coretemp-isa-0000         # one chip by name
```

Thermal failure signatures and triage:

```text
Symptom: Board reboots or NVMe resets after 10-20 minutes under load.
  - Check dmesg: "CPU0: Package temperature above threshold, cpu clock throttled"
  - Check sensors: if Package temp approaches temp1_crit (often 95-105 C), thermal solution
    is inadequate.
  - Check fan RPM via fan1_input: if zero, the fan is dead or the connector is unseated.
  - Check INA3221 current: if input current is below expected draw, PSU may be current-limiting.
  Action: re-apply thermal paste; verify heatsink mounting pressure; confirm fan direction.

Symptom: Temperatures read correctly in sensors but PCIe errors appear at moderate load.
  - Check /sys/class/hwmon/hwmonN/in1_input for 12V / 3.3V rails on the board's INA sensors.
  - A rail sagging 5% or more under load causes PCIe receiver noise (BadTLP / RxErr AER events).
  Action: measure rail voltage with a scope; check bulk capacitance near the PCIe slot.

Symptom: All temperatures look fine but thermal throttling message appears.
  - Some platforms trigger throttle based on the PCH or memory temperature, not just the CPU.
  - cat /sys/class/thermal/thermal_zone*/type to identify which zone is triggering.
  Action: check the specific zone's _crit threshold; check airflow around that component.
```

### cpufreq: CPU Frequency and Thermal Throttling

```bash
# Current frequency per CPU (in kHz):
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq   # all CPUs

# Available governors:
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors
# performance, powersave, schedutil, ondemand, conservative

# Change governor for all CPUs:
echo "performance" | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
# Use "performance" on test stations to prevent thermal throttling from affecting results.

# Min/max limits:
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq
echo 3600000 | sudo tee /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq   # 3.6 GHz cap

# Check for throttling:
dmesg | grep -i throttl
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq  # if << max, likely throttled
```

### RAPL: Running Average Power Limit

RAPL exposes CPU and Dynamic Random-Access Memory (DRAM) power consumption counters. On test stations they help characterize power draw during load.

```bash
# RAPL domains are exposed via powercap:
ls /sys/class/powercap/

# Energy counters (in microjoules -- monotonically increasing, wraps at max_energy_range_uj):
cat /sys/class/powercap/intel-rapl:0/energy_uj             # package 0 energy
cat /sys/class/powercap/intel-rapl:0:0/energy_uj           # core domain
cat /sys/class/powercap/intel-rapl:0:1/energy_uj           # uncore/DRAM domain
cat /sys/class/powercap/intel-rapl:0/name                  # domain name

# Measure power over an interval (two readings, then compute delta):
e1=$(cat /sys/class/powercap/intel-rapl:0/energy_uj); sleep 1
e2=$(cat /sys/class/powercap/intel-rapl:0/energy_uj)
awk "BEGIN{printf \"Package power: %.2f W\n\", ($e2-$e1)/1e6}"

# Power limits (current TDP envelope):
cat /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw  # PL1 in microwatts
cat /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw  # PL2

# turbostat (linux-tools package) gives a unified per-CPU view of frequency + power + temp:
sudo turbostat --interval 1   # 1-second samples with per-package and per-core columns
```

---

## perf and ftrace Basics

### perf

`perf` reads hardware performance counters (available via the PMU) and software tracepoints. Even a brief `perf stat` run identifies whether a workload is CPU-bound, memory-bound, or dominated by cache misses.

```bash
# Count events for a command:
perf stat ./workload
# Key output lines:
#   task-clock (msec): CPU time consumed
#   instructions:      total instructions executed
#   cycles:            total cycles
#   IPC (insn/cycle):  < 1 = stalled (usually memory or branch prediction); > 2 = good
#   cache-misses:      LLC miss rate
#   branch-misses:     branch predictor misses

# Live top (hottest functions right now):
sudo perf top                     # system-wide
sudo perf top -p <pid>            # one process

# Record a call-graph profile:
sudo perf record -g -p <pid> sleep 10   # record for 10 s
sudo perf report                        # interactive browser of the hotspots

# Count specific events:
perf stat -e cache-misses,cache-references,branch-misses,instructions ./workload

# perf on a device driver:
sudo perf trace -a -e 'irq:irq_handler_entry' sleep 5   # trace all IRQ handler entries for 5s
```

### ftrace

ftrace is built into the kernel and requires no extra packages. It traces kernel functions and tracepoints with very low overhead -- useful for debugging driver behavior or latency spikes.

```bash
# Mount debugfs if not already mounted:
sudo mount -t debugfs none /sys/kernel/debug

cd /sys/kernel/debug/tracing

# Function tracing:
echo function | sudo tee current_tracer    # enable the function tracer
echo "nvme_*" | sudo tee set_ftrace_filter # trace only nvme_ functions
echo 1 | sudo tee tracing_on              # start recording
sleep 5
echo 0 | sudo tee tracing_on              # stop
cat trace                                  # read the log

# Event tracing (tracepoints):
ls available_events | grep nvme           # available NVMe tracepoints
echo "nvme:nvme_sq" | sudo tee set_event  # enable a specific tracepoint
echo 1 | sudo tee tracing_on
sleep 5
echo 0 | sudo tee tracing_on
cat trace | head -50

# Latency tracing (find the highest-latency function call):
echo wakeup | sudo tee current_tracer
echo 1 | sudo tee tracing_on
sleep 5
cat trace | head -100   # look for "LATENCY TRACE" header and max latency value

# Reset tracer:
echo nop | sudo tee current_tracer
echo "" | sudo tee set_ftrace_filter
```

---

## Filesystems, Mount, and /proc/mounts

```bash
# Inventory:
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL
sudo fdisk -l                      # disks and partition tables
sudo parted -l                     # GPT-aware, more detail
blkid                              # UUIDs and filesystem types (used in /etc/fstab)

# Usage:
df -h                              # filesystem usage: how full each FS is (human-readable)
df -i                              # INODE usage (a FS can be "full" on inodes with space left)
du -sh /var/log/*                  # size of each item under a directory
du -sh /opt/test 2>/dev/null       # total size of a tree

# Mount / unmount:
sudo mount /dev/sdb1 /mnt/usb      # mount a partition
sudo mount -o ro /dev/sdb1 /mnt/usb  # read-only (forensics / imaging)
sudo umount /mnt/usb               # unmount (device must not be in use)
mount | column -t                  # all current mounts, aligned
cat /etc/fstab                     # persistent mount config (entries mounted at boot)

# Create / check (destructive -- double-check the target device!):
sudo mkfs.ext4 /dev/sdb1
sudo fsck /dev/sdb1                # check / repair (FS must be UNMOUNTED)

# SMART health -- the storage equivalent of AER for PCIe:
sudo smartctl -H /dev/sda          # overall: PASSED / FAILED
sudo smartctl -a /dev/sda          # all SMART attributes (reallocated sectors, pending, offline)
sudo nvme smart-log /dev/nvme0     # NVMe: temp, percentage_used, media_errors, critical_warning
sudo nvme error-log /dev/nvme0     # NVMe error log entries
```

---

## Permissions and Linux Capabilities

```bash
# Permission string from ls -l:  -rwxr-xr-x
#   char 1:     type ( - file, d dir, l symlink, c char device, b block device )
#   chars 2-4:  OWNER permissions  rwx
#   chars 5-7:  GROUP permissions  rwx
#   chars 8-10: OTHER permissions  rwx
#   r = read (4)  w = write (2)  x = execute (1)

id                       # your uid, gid, and group memberships
whoami                   # current username
groups testeng           # groups a user belongs to

chmod 755 script.sh      # rwxr-xr-x  standard for executable scripts
chmod 644 config.json    # rw-r--r--  standard for regular files
chmod 600 id_ed25519     # rw-------  required for SSH private keys
chmod +x deploy.sh       # add execute for everyone
chmod -R 755 /opt/test   # recursive
chmod u+s /usr/bin/tool  # SETUID: binary runs with file owner's privileges
chmod g+s shared_dir/    # SETGID on a dir: new files inherit the directory's group
chmod +t /tmp            # STICKY bit: only owner can delete their files in this dir

sudo chown testeng:eng file
sudo chown -R testeng:eng /opt/test/
sudo usermod -aG dialout testeng   # add to the dialout group (access /dev/ttyUSB* without sudo)

# Linux Capabilities: grant specific root-like privileges without full root.
# More secure than setuid root or running as root.
getcap /usr/bin/ping                                   # show capabilities on a file
sudo setcap cap_net_raw+eip /opt/atf/packet_gen        # grant raw socket access
sudo setcap cap_sys_rawio+eip /opt/atf/hwmon_reader    # grant raw I/O port access
# Relevant capabilities for test tools:
#   cap_net_raw     : raw/packet sockets (tcpdump, custom NIC test)
#   cap_sys_rawio   : raw I/O port access (pcimem, direct MMIO)
#   cap_ipc_lock    : lock memory pages (latency-sensitive tests)
#   cap_sys_admin   : broad admin (loading modules, mounting, sysfs writes) -- avoid; too broad
capsh --print                                          # capabilities of the current process
```

---

## Building and Booting: initramfs, ARM Device Tree, Serial Console

### initramfs

The initramfs is a minimal filesystem baked into the boot image. It loads early drivers (NVMe, RAID, filesystem) before the real root is accessible.

```bash
# Rebuild initramfs (after changing kernel parameters or blacklists):
sudo update-initramfs -u                # Debian/Ubuntu: update for the running kernel
sudo update-initramfs -u -k all         # rebuild for ALL installed kernels
sudo dracut -f                          # RHEL/Fedora equivalent
sudo dracut -f --kver $(uname -r)       # specific kernel version

# Inspect contents of an initramfs:
mkdir /tmp/initrd_inspect && cd /tmp/initrd_inspect
gzip -cd /boot/initrd.img-$(uname -r) | cpio -idmv 2>/dev/null | head -50
# Or with unmkinitramfs (Debian):
unmkinitramfs /boot/initrd.img-$(uname -r) /tmp/initrd_inspect

# Force-include a specific module in initramfs (e.g. a new NVMe driver for early boot):
echo "nvme_core" >> /etc/initramfs-tools/modules   # Debian/Ubuntu
sudo update-initramfs -u

# GRUB command line (kernel boot parameters):
cat /proc/cmdline                  # the ACTUAL parameters the running kernel was given
# Edit /etc/default/grub (GRUB_CMDLINE_LINUX_DEFAULT), then:
sudo update-grub                   # regenerate /boot/grub/grub.cfg
```

### ARM Device Tree

On ARM compute boards (common at Zoox), the kernel does not discover hardware by probing bus addresses; instead, a Device Tree Blob (DTB) describes every peripheral -- I2C buses, SPI controllers, PCIe root ports, GPIO pins, power domains -- and the kernel reads it at boot.

```bash
# Find the loaded DTB:
ls /boot/*.dtb /boot/dtbs/                     # Debian/Ubuntu path
ls /proc/device-tree/                          # live kernel-parsed device tree (virtual FS)

# Inspect the live device tree:
ls /proc/device-tree/                          # top-level nodes
cat /proc/device-tree/model | tr -d '\0'       # board model string
cat /proc/device-tree/compatible | tr '\0' '\n'  # compatible strings (vendor,chip)

# Decompile a DTB to DTS (human-readable source):
dtc -I dtb -O dts -o board.dts /boot/board.dtb
# Now you can grep or read board.dts to understand the hardware topology.

# Recompile a DTS back to DTB:
dtc -I dts -O dtb -o board.dtb board.dts

# Overlay DTBOs (dynamic device tree overlays -- common for optional add-ons):
ls /boot/overlays/                             # available overlays
# Active overlays are applied by U-Boot or the boot loader config:
cat /boot/config.txt                           # Raspberry Pi example (dtoverlay=...)
# On Tegra/Qualcomm boards, overlays may be applied by the early-stage bootloader

# Debugging DT binding failures:
dmesg | grep -i 'of\|dtb\|device tree\|compatible'
ls /sys/bus/platform/devices/                  # platform devices created by the DT
cat /sys/bus/platform/devices/3110000.i2c/driver  # which driver is bound (symlink)
```

Common device tree debug patterns:

```bash
# "The I2C temperature sensor is not detected":
cat /proc/device-tree/i2c@3110000/tmp102@48/compatible   # is the node present?
dmesg | grep "tmp102"                          # did the driver probe?
i2cdetect -y 0                                 # is the device actually on the bus?

# "The PCIe controller won't enumerate devices":
dmesg | grep -i 'pci\|pcie'                   # look for DWC/IPROC/Tegra PCIe init messages
ls /proc/device-tree/pcie/                     # does the DT have a PCIe node?
cat /proc/device-tree/pcie/compatible | tr '\0' '\n'  # is the compatible string matched by a driver?

# "I need to add a new peripheral":
# 1. Write or modify the DTS (add the device node under the correct bus node).
# 2. Build/install the driver module.
# 3. Compile the DTS to DTB.
# 4. Boot with the new DTB (copy to /boot, update U-Boot env or GRUB).
# 5. Check dmesg for driver probe.
```

### Serial Console

Serial console is the lifeline when SSH is not available -- during bring-up, firmware flashing, early boot failures, or a wedged board.

```bash
# Connect to a device via USB serial adapter:
# First: identify the port
ls -l /dev/ttyUSB* /dev/ttyACM*   # USB serial adapters
dmesg | grep -i tty | tail -10    # driver messages when the cable was plugged in

# minicom (feature-rich, saves settings):
sudo apt install minicom
minicom -s                         # setup: set port to /dev/ttyUSB0, baud to 115200
minicom -D /dev/ttyUSB0 -b 115200  # connect directly
# Exit minicom: Ctrl+A, then X

# screen (simple, always installed):
sudo screen /dev/ttyUSB0 115200
# Exit screen: Ctrl+A, then K (kill), confirm

# picocom (minimal, good for scripts):
sudo apt install picocom
picocom -b 115200 /dev/ttyUSB0
# Exit: Ctrl+A then Ctrl+X

# cu (basic, part of uucp):
cu -l /dev/ttyUSB0 -s 115200

# Save serial output to a file while also viewing it:
picocom -b 115200 /dev/ttyUSB0 | tee serial_$(date +%Y%m%d_%H%M%S).log

# Common baud rates for embedded boards: 115200, 57600, 38400, 19200
# Most Tegra, Qualcomm, and similar platforms default to 115200 8N1.

# If the port exists but garbled output:
# 1. Verify baud rate in the bootloader config (U-Boot: "bdinfo" shows baudrate).
# 2. Check parity/stop bits: 8N1 is standard but some boards use 8E1.
# 3. Check for flow control: disable RTS/CTS unless the board requires it.
```

---

## Recovering a Wedged Board

This is the escalating sequence when a board stops responding.

### Step 1: Diagnose Before Acting

```bash
# Can you still ping it?
ping -c3 192.168.100.10

# Can you reach it via serial console?
# If yes: try to connect and check dmesg / systemd status before resetting.

# Is it SSH-responsive but sluggish?
ssh testeng@192.168.100.10 'uptime; free -h; dmesg -T | tail -20'
```

### Step 2: Graceful Recovery

```bash
# Attempt graceful reboot via SSH:
ssh testeng@192.168.100.10 'sudo reboot'

# If SSH hangs: send magic SysRq (requires ssh access or serial console):
# Enable SysRq first (usually on by default): echo 1 > /proc/sys/kernel/sysrq
echo b > /proc/sysrq-trigger          # immediate reboot (no sync or unmount)
echo s > /proc/sysrq-trigger          # sync filesystems to disk
echo u > /proc/sysrq-trigger          # remount all filesystems read-only
echo o > /proc/sysrq-trigger          # power off

# The "REISUB" sequence via serial console or keyboard (safe, remounts FS before reboot):
# Hold Alt+SysRq and press: R E I S U B  (one per second)
# R: unraw keyboard; E: SIGTERM all; I: SIGKILL all; S: sync; U: remount ro; B: reboot
```

### Step 3: Hard Power Cycle

```bash
# If the board has a BMC / IPMI:
ipmitool -H 192.168.100.20 -U admin -P password chassis power status
ipmitool -H 192.168.100.20 -U admin -P password chassis power off
ipmitool -H 192.168.100.20 -U admin -P password chassis power on
ipmitool -H 192.168.100.20 -U admin -P password chassis power cycle  # off then on

# If GPIO-controlled power on the test fixture:
# NOTE: The /sys/class/gpio sysfs ABI is deprecated since kernel 4.8.
# Prefer libgpiod (gpioset / gpioget / gpiomon) for new code.
# The sysfs interface still works on most distros but may disappear in future kernels.
echo out > /sys/class/gpio/gpio17/direction
echo 0 > /sys/class/gpio/gpio17/value   # assert reset or cut power
sleep 2
echo 1 > /sys/class/gpio/gpio17/value   # release

# Modern equivalent with libgpiod (install: apt install gpiod):
# gpioset gpiochip0 17=0   # assert
# sleep 2
# gpioset gpiochip0 17=1   # release

# If a relay-controlled PSU:
# Write the PSU control script that sets the relay line low/high.
```

### Step 4: Post-Recovery Triage

```bash
# After the board comes back up, check the previous boot's evidence:
journalctl -b -1                          # all messages from the previous boot
journalctl -k -b -1                       # kernel messages from the previous boot
journalctl -b -1 -p err                   # only errors
journalctl -b -1 --since "$(date -d '2 hours ago' '+%Y-%m-%d %H:%M:%S')"

# Check for filesystem errors from an unclean shutdown:
dmesg | grep -i 'ext4\|xfs\|btrfs\|journal\|filesystem'   # FS recovery messages
sudo fsck /dev/sda1                        # run fsck on the root FS (unmounted; from live USB)

# Check for hardware errors:
dmesg -T | grep -iE 'mce|error|uncorrect|fatal'
# MCE = Machine Check Exception: the CPU detected an internal hardware fault
# (corrected vs. uncorrected; uncorrected MCE on memory or cache = failing hardware)
```

### Filesystem Full / Inode Full

Both present as "no space left on device" from the application's perspective:

```bash
df -h                              # which FS is full (check % column)
df -i                              # which FS has run out of inodes (% iuse column)

# Find the biggest consumers (when disk is full):
du -sh /var/log/* | sort -rh | head -20   # largest directories under /var/log
find /var/log -name '*.log' -size +100M   # log files over 100 MB
journalctl --disk-usage               # how much the journal uses
sudo journalctl --vacuum-time=7d       # trim journal to 7 days

# When inodes are exhausted (many small files):
find /tmp -type f | wc -l              # count files in /tmp
ls /var/spool/                         # sometimes a runaway print queue, cron output, etc.
```

---

## systemd Service Management

`systemd` is PID 1 on modern Linux. It starts services in dependency order, restarts crashed services, and collects all their logs into the journal.

```bash
systemctl start atf-dashboard
systemctl stop atf-dashboard
systemctl restart atf-dashboard
systemctl reload atf-dashboard         # re-read config without restart (if the service supports it)
systemctl status atf-dashboard         # state + PID + uptime + last log lines
systemctl enable atf-dashboard         # start at boot
systemctl disable atf-dashboard
systemctl enable --now atf-dashboard   # enable AND start in one command
systemctl is-active atf-dashboard      # prints "active"; exit 0 if running (use in scripts)
systemctl is-enabled atf-dashboard     # will it start at boot?
systemctl list-units --type=service    # all loaded services
systemctl list-units --failed          # services that FAILED (check this after a bad boot)
systemctl cat atf-dashboard            # display the effective unit file(s)
```

### Writing a Service Unit

```bash
# Place in /etc/systemd/system/ for services you create.
# /lib/systemd/system/ comes from packages; override with drop-ins, don't edit those.
sudo tee /etc/systemd/system/atf-dashboard.service >/dev/null << 'EOF'
[Unit]
Description=ATF Manufacturing Test Dashboard
After=network.target
# Wants=postgresql.service    # soft dependency: start postgres too, but don't fail if it dies
# Requires=postgresql.service # hard dependency: if postgres stops, this stops too

[Service]
# Type options:
#   simple  : the ExecStart process IS the service (default)
#   forking : daemon double-forks; systemd waits for the fork
#   oneshot : runs once and exits (use for scripts, health checks)
#   notify  : service tells systemd via sd_notify when ready (most robust)
Type=simple
ExecStart=/usr/bin/python3 -m uvicorn dashboard.server:app --host 0.0.0.0 --port 8080
ExecReload=/bin/kill -SIGHUP $MAINPID
WorkingDirectory=/opt/atf/dashboard
Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60
User=testeng
Group=testeng
Environment=PYTHONUNBUFFERED=1
# StandardOutput=journal   (default)
# StandardError=journal    (default)

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload         # REQUIRED after creating or editing unit files
sudo systemctl enable --now atf-dashboard

# Override ONE setting without editing the unit (creates a drop-in override.conf):
sudo systemctl edit atf-dashboard
# Add: [Service] \n Environment=DEBUG=1
# Then: sudo systemctl daemon-reload && sudo systemctl restart atf-dashboard
```

---

## Process Management and Signals

```bash
ps aux                              # all processes (USER PID %CPU %MEM ... COMMAND)
ps -ef --forest                     # process tree showing parent/child relationships
ps aux | grep '[p]ython'            # the [p] trick excludes the grep process itself
pgrep -f test_runner                # PIDs matching full command line
pgrep -la python                    # -l name, -a full command line
pidof python3                       # exact name match
top                                 # live: M = sort by mem, P = by CPU, 1 = per-core, k = kill
htop                                # nicer interactive view (if installed)

# Signals:
kill -15 <pid>     # SIGTERM: ask politely (default). Process can clean up. Use first.
kill -9  <pid>     # SIGKILL: force. Cannot be caught/ignored. No cleanup. Last resort.
kill -2  <pid>     # SIGINT:  same as Ctrl+C
kill -1  <pid>     # SIGHUP:  many daemons reload config on this
kill -0  <pid>     # no signal sent; tests whether the PID exists (exit 0 = exists)
killall python3    # kill by name
pkill -f 'pytest'  # kill by command-line pattern

# Priority:
nice -n 10 ./job                  # start at lower priority (higher number = nicer = lower prio)
sudo renice -n -5 -p <pid>        # raise priority of running process (needs root for negative)
taskset -c 0,1 ./workload         # pin to CPUs 0 and 1

# Job control:
./script.sh &                     # background
nohup ./script.sh &               # keep running after logout (output -> nohup.out)
jobs                              # list background jobs in this shell
fg %1                             # bring job 1 to foreground
disown %1                         # detach from shell so it survives shell exit

# Diagnosing a hung process:
strace -p <pid>                   # show system calls -- the blocking call IS the root cause
cat /proc/<pid>/status            # state, memory, thread count
cat /proc/<pid>/cmdline | tr '\0' ' '   # full command line
ls -l /proc/<pid>/fd              # open file descriptors (what it has open)
```

`strace` deserves emphasis: when a Python test or instrument driver hangs, `strace -p <pid>` shows the exact system call it is stuck in. A `read()` on a socket fd means it is waiting for an instrument response that never came. A `poll()` on a serial fd means the device is not replying. That observation localizes a "test just hangs" bug in seconds, without touching a line of code.

---

## Networking for Test Engineers

```bash
# ip (modern, always installed):
ip -br link                          # one-line-per-interface with state
ip addr show                         # interfaces with IP addresses
ip route show                        # routing table (look for "default via <gateway>")
ip neigh show                        # ARP/neighbor table (IP -> MAC)
ip -s link show eth0                 # statistics: RX/TX packets, errors, drops

# Make temporary changes:
sudo ip addr add 192.168.1.50/24 dev eth0
sudo ip link set eth0 mtu 9000       # jumbo frames

# ethtool (NIC link-layer truth):
ethtool eth0                         # speed, duplex, link, autoneg
ethtool -S eth0                      # hardware statistics: rx_errors, tx_errors, drops, CRC
                                     # ANY non-zero error counter = PHY, cable, SFP, or driver issue
ethtool -i eth0                      # driver name + driver/firmware versions
ethtool -m eth0                      # SFP/QSFP optical power, temp (if supported)
ethtool -p eth0 10                   # blink the port LED for 10 s (find the right cable)

# ethtool failure signatures:
# rx_crc_errors > 0        -> physical layer issue (bad cable, SFP, or switch port)
# rx_missed_errors > 0     -> NIC ring buffer overrun under load; increase ring size:
#                             ethtool -G eth0 rx 4096
# tx_errors / tx_carrier_errors -> link dropped during TX; check cable and SFP power levels
# "Link detected: no"      -> no carrier; check cable, switch port, SFP insertion
# Speed 100Mb/s when 10Gb expected -> auto-neg fell back; check switch config or force speed:
#                             ethtool -s eth0 speed 10000 duplex full autoneg off

# Sockets (ss replaces netstat):
ss -tuln                             # TCP+UDP listening, numeric -- "what ports are open?"
ss -tlnp '( sport = :8080 )'         # who is listening on 8080
lsof -i :8080                        # alternative: what process holds port 8080

# Connectivity checklist (work bottom up):
# 1. ip link show  (state UP? carrier?)
# 2. ethtool eth0  (link detected? speed/duplex correct?)
# 3. ip addr       (correct IP/mask?)
# 4. ping <gateway>
# 5. dig <hostname>
# 6. ss -tuln      (target port open?)
# 7. curl -v http://host:port/health
# 8. sudo iptables -L  (rule blocking it?)

# Packet capture:
sudo tcpdump -i eth0 -c 100 -w cap.pcap     # capture 100 packets to file (open in Wireshark)
sudo tcpdump -i eth0 port 502               # Modbus/TCP filter
tcpdump -r cap.pcap | head

# Throughput test:
iperf3 -s                                   # server
iperf3 -c 192.168.1.100 -t 30               # TCP bandwidth test, 30 s
iperf3 -c 192.168.1.100 -u -b 10G           # UDP at 10 Gbps target (loss/jitter)
```

---

## Hardware Debug Command Reference

The quick-reach section for when a board is on the fixture throwing errors. Organized by the question you are answering.

### "Is the Device Present and on What Link?"

```bash
lspci -nn                                        # all devices with numeric IDs
lspci -d 10de: -nn                               # only NVIDIA devices
lspci -t                                         # topology tree
lspci -s 03:00.0 -vvv | grep -i 'lnkcap\|lnksta'  # capable vs actual speed/width
cat /sys/bus/pci/devices/0000:03:00.0/current_link_speed
cat /sys/bus/pci/devices/0000:03:00.0/max_link_speed
lspci -k -s 03:00.0                              # which driver is bound
lsusb -t; nvme list; lsblk                       # USB tree, NVMe namespaces, block devices
lshw -short                                      # one-line full inventory
```

### "Is the Link Degraded or Erroring?"

```bash
# Degradation = current != max:
for d in /sys/bus/pci/devices/*/; do
    c=$(cat "$d/current_link_speed" 2>/dev/null) || continue
    m=$(cat "$d/max_link_speed"     2>/dev/null) || continue
    [[ "$c" != "$m" ]] && echo "SPEED DEGRADED ${d##*devices/}: $c (max $m)"
done

# AER error counters (kernel-tracked):
cat /sys/bus/pci/devices/0000:03:00.0/aer_dev_correctable
cat /sys/bus/pci/devices/0000:03:00.0/aer_dev_nonfatal
cat /sys/bus/pci/devices/0000:03:00.0/aer_dev_fatal
lspci -s 03:00.0 -vvv | grep -A12 'Advanced Error Reporting'
dmesg -T | grep -iE 'aer|pcie|corrected|malformed'
```

### "What Is the Kernel Saying?"

```bash
dmesg -T | tail -50
dmesg -T | grep -iE 'error|fail|fault|timeout|reset'
dmesg -T | grep -i nvme                          # NVMe resets/timeouts
dmesg -T | grep -i 'thermal\|throttl'            # thermal throttling
dmesg -w                                         # follow live during a stress run
sudo dmesg -C                                    # clear before a test, re-read after
journalctl -k -b                                 # persisted kernel log for this boot
```

### "Storage Health?"

```bash
nvme list
sudo nvme smart-log /dev/nvme0                   # temp, percentage_used, media_errors, crit
sudo nvme error-log /dev/nvme0                   # error log entries
sudo smartctl -H /dev/sda                        # SATA: PASSED / FAILED
sudo smartctl -a /dev/sda                        # full SMART attributes
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL,SERIAL
```

### "Network Link?"

```bash
ip -br link                                      # up/down at a glance
ethtool eth0                                     # speed, duplex, autoneg
ethtool -S eth0 | grep -iE 'err|drop|crc'        # error counters (all should be 0)
ethtool -i eth0                                  # driver + firmware version
cat /sys/class/net/eth0/carrier                  # 1 = link detected
ping -c4 <gateway>; ss -tuln
```

### "Thermals, Power, CPU, Load?"

```bash
sensors                                          # lm-sensors: temps, fans, voltages
paste <(cat /sys/class/thermal/thermal_zone*/type) \
      <(cat /sys/class/thermal/thermal_zone*/temp) | \
      awk '{printf "%-22s %.1f C\n", $1, $2/1000}'
sudo turbostat --interval 1 --num_iterations 3   # per-package power, frequency, temp
lscpu; nproc                                     # CPU topology
free -h                                          # memory usage
uptime                                           # load averages (compare to nproc)
cat /proc/interrupts | grep -iE 'nvme|eth'       # are device IRQs firing and spread?
```

### "Perf Bottleneck?"

```bash
vmstat 1 5                                       # CPU vs memory (si/so) vs IO-wait (wa)
iostat -x 1                                      # per-disk %util / await
mpstat -P ALL 1                                  # per-core usage (find one pegged core)
pidstat 1                                        # per-process CPU over time
strace -p <pid>                                  # what a hung/slow process is blocked on
sudo perf top -p <pid>                           # which functions are eating CPU
```

### "Driver/Firmware OK?"

```bash
lspci -k -s 03:00.0                              # is the right driver bound?
lsmod | grep <driver_name>                       # is the module loaded?
modinfo <driver_name> | grep version             # what version?
ethtool -i eth0                                  # NIC firmware version
nvme id-ctrl /dev/nvme0 | grep -i 'fr\|sn\|mn'  # NVMe firmware rev, serial, model
dmesg | grep -i <driver_name>                    # driver init / bind / error messages
```

### The Mental Checklist

When a board fails on the fixture, drive toward the **failure tuple**, not a one-word verdict. "It failed" is useless; "endpoint `0000:03:00.0` trained Gen3 x8 instead of Gen4 x16, correctable AER BadTLP counter climbing at 3/sec under load, package temp 78 C, NVMe SMART clean" is something an Electrical Engineering (EE) can act on.

1. **Present?** `lspci -nn` / sysfs -- does it enumerate at all?
2. **Right link?** `current_link_speed` / `current_link_width` vs `max_*` -- degraded?
3. **Erroring?** AER counters + `dmesg -T` -- which specific bits, how fast accumulating?
4. **Healthy peripherals?** `nvme smart-log`, `ethtool -S` -- clean counters?
5. **Environment?** `sensors` / thermal zones, `vmstat` / `iostat` -- hot or resource-starved?
6. **Driver/firmware?** `lspci -k`, `ethtool -i`, `modinfo` -- right versions bound?
7. **Capture the evidence:** `tee` every output into a timestamped log file; hand the decoded failure tuple to the EE, not a one-word verdict.
