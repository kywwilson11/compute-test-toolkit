## Embedded Systems for the Compute Test Engineer

Embedded systems are not the center of this role, but they surround it. An autonomous-vehicle compute platform is itself a large embedded system, and scattered around the big SoCs and GPUs are dozens of microcontrollers you will test through or alongside: the BMC that owns power sequencing and telemetry, the safety MCU that watches the compute, power-sequencer and PMIC controllers, fan controllers, and the MCUs inside cameras, GMSL deserializers, and sensors. Every one of them runs firmware, talks a low-speed bus, and has a bring-up and update story. A test engineer who is fluent in embedded — registers, real-time behavior, firmware flashing, ESD — reads board behavior faster, writes better fixtures, and debugs the seams between "the chip" and "the software" where manufacturing defects love to hide.

This chapter is the embedded layer beneath the rest of the guide. It cross-references rather than repeats: the low-speed buses are covered in depth in the Automotive and Serial Buses chapter, rails and bring-up phases in the Power, Bring-up, and Functional Safety chapter, fixture control loops in the Control Systems chapter, and register/bit-field mechanics in the PCIe and Python chapters.

---

## What "Embedded" Means on a Compute Platform

A useful split:

- **MCU (microcontroller)** — CPU + flash + SRAM + peripherals on one die, runs bare-metal or an RTOS from on-chip flash, boots in milliseconds, deterministic. Think BMC, power sequencer, safety supervisor. Tens of KB to a few MB of memory, no MMU (or a simple MPU for protection regions).
- **MPU / application processor** — external DRAM, an MMU, runs Linux. The big compute SoC is here. Covered by the Linux chapter.
- **SoC** — a system-on-chip blends both: application cores plus on-die microcontrollers (a safety island, a sensor hub, power-management cores) running their own firmware.

The distinctions that matter at test: an MCU's behavior is **deterministic and owned by its firmware**, it comes up before Linux does (so it is often what you talk to *first* during bring-up), and its failures are register- and timing-level, not log-file-level. When a board "won't power on," the answer usually lives in an MCU's firmware and a power-sequencing register, not in `dmesg`.

Where MCUs show up on an AV compute board, and why you care:

| MCU role | What it owns | Test-engineer touchpoint |
|---|---|---|
| **BMC** (board management controller) | power sequencing, fan/thermal, rail telemetry, recovery | read rails/temps unattended over IPMI/sysfs; firmware version is part of genealogy |
| **Safety MCU / supervisor** | watchdog of the compute, fault reaction | fault-injection response, watchdog behavior, ASIL evidence |
| **PMIC / sequencer** | rail enable order and timing | the power-on sequence is "the datasheet is law" (Power chapter) |
| **Sensor / camera / GMSL MCUs** | sensor config, link bring-up | I2C config sequences, firmware revs, link training |

---

## Microcontrollers and the Memory Map

You already think in registers from PCIe config space; embedded is the same skill one level lower. An MCU exposes its peripherals as **memory-mapped registers**: fixed addresses where a load reads hardware state and a store changes hardware behavior. There is no driver stack in the way — you read and write the silicon directly.

```c
/* A memory-mapped 32-bit peripheral register. 'volatile' is mandatory: it tells the
 * compiler the value can change outside program flow (hardware sets it) and must not
 * be cached in a register or optimized away. Omitting volatile is the classic
 * embedded bug -- a poll loop that never sees the bit change. */
#define GPIOA_BASE   0x40020000u
#define GPIO_ODR     (*(volatile uint32_t *)(GPIOA_BASE + 0x14))  /* output data reg */
#define GPIO_IDR     (*(volatile uint32_t *)(GPIOA_BASE + 0x10))  /* input data reg  */

GPIO_ODR |=  (1u << 5);          /* set pin 5 high  (read-modify-write) */
GPIO_ODR &= ~(1u << 5);          /* clear pin 5     */
while (!(GPIO_IDR & (1u << 3)))  /* spin until input pin 3 reads high   */
    ;
```

The bit-field extract/insert idioms are identical to the PCIe register work — `(reg >> lsb) & mask` to read a field, `reg = (reg & ~(mask << lsb)) | (val << lsb)` to write one. Two embedded-specific hazards:

- **Read-modify-write races.** `REG |= bit` is three operations (load, OR, store). If an interrupt also writes `REG` between the load and the store, one update is lost. Guard shared registers with a critical section, or use hardware atomic set/clear registers where the silicon provides them (many MCUs expose separate "set" and "clear" register aliases exactly to avoid RMW).
- **Write-1-to-clear status bits** — the same semantics as PCIe AER status: you clear a latched flag by writing a 1 to it, not a 0. Writing the whole register back can clear bits you did not mean to. (The PCIe chapter and the toolkit's BERT engine lean on this.)

Memory layout is fixed by a **linker script** that places code/constants in flash and variables/stack in SRAM, and defines the regions the startup code initializes (copy `.data` from flash to RAM, zero `.bss`) before `main()` runs. Knowing the map is how you read a fault address: a hard fault at an address outside any valid region is a wild pointer; one inside the peripheral block is a bad register access.

---

## Bare-Metal, RTOS, and Real-Time

There are three execution models, in increasing order of structure:

- **Bare-metal super-loop** — `init(); for(;;) { do_work(); }` with interrupts handling time-critical events. Simple, fully deterministic, no scheduler overhead. Fine for a power sequencer or a fan controller.
- **RTOS** (FreeRTOS, Zephyr, ThreadX) — preemptive **tasks** with priorities, a scheduler, queues/semaphores/mutexes for inter-task communication. You reach for it when you have several activities at different rates and priorities (telemetry, comms, control) that a super-loop can no longer juggle cleanly.
- **Embedded Linux** — full MMU, processes, drivers; the application SoC. The Linux chapter covers it.

**Real-time means deterministic, not fast.** A hard-real-time task must meet its deadline *every* time; missing it is a system failure (a motor controller, a safety reaction). Soft real-time tolerates occasional misses (a UI, a log flush). What you reason about:

- **Latency and jitter** — the delay from event to response, and its variation. Jitter is often worse than latency: a control loop can design around a fixed 100 µs delay but not around one that randomly spikes to 5 ms.
- **WCET (worst-case execution time)** — hard-real-time analysis is about the worst case, not the average. Caches, branch prediction, and DMA contention make the worst case much larger than the typical case.
- **Priority inversion** — a high-priority task blocked on a mutex held by a low-priority task that itself is preempted by a medium-priority task. The classic fix is **priority inheritance** (the holder temporarily inherits the waiter's priority). This is famous because it stalled the Mars Pathfinder.

Two safety mechanisms you will meet on every compute board:

- **Watchdog timer** — a counter the firmware must "kick" periodically; if the firmware hangs and stops kicking, the watchdog resets the system. The safety MCU's watchdog over the compute is part of the fault-reaction path (Functional Safety section of the Power chapter). At test, you verify the watchdog actually fires when starved — a watchdog that never bites is worse than none, because it gives false confidence.
- **Brown-out detection** — holds the MCU in reset until the supply is in spec, so it never executes on a marginal rail. A board that resets under load, not at power-on, often points here.

---

## Interrupts and Concurrency on an MCU

Interrupts are the embedded concurrency model, and they bite the same way threads do (the Python chapter's race conditions, one layer down and with no GIL to soften them).

```c
/* ISR + main-loop hand-off via a ring buffer. The shared indices are volatile; the
 * ISR is the sole producer, main() the sole consumer (single-producer/single-consumer
 * is lock-free if head/tail are written by only one side each). */
#define RB_SZ 256
static volatile uint8_t  rb[RB_SZ];
static volatile uint16_t head, tail;          /* head: ISR writes; tail: main writes */

void UART_RX_IRQHandler(void) {               /* keep ISRs SHORT: no printf, no malloc, no blocking */
    uint16_t next = (head + 1) % RB_SZ;
    if (next != tail) { rb[head] = UART_DR; head = next; }   /* drop on full, never block */
}

int main(void) {
    for (;;) {
        if (tail != head) {                   /* data available */
            uint8_t byte = rb[tail];
            tail = (tail + 1) % RB_SZ;
            process(byte);
        }
    }
}
```

The rules that keep this correct:

- **ISRs must be short and non-blocking** — no `printf`, no dynamic allocation, no waiting on a lock. Do the minimum (grab the byte, set a flag) and let the main loop do the work.
- **`volatile` on anything shared with an ISR**, or the compiler will cache the stale value in a register.
- **Critical sections** — to touch a multi-byte shared structure from both ISR and main safely, briefly disable interrupts (`__disable_irq()/__enable_irq()`), or use a hardware atomic. Keep them as short as possible; disabling interrupts adds latency to everything else.
- **`volatile` is not a memory barrier.** It prevents caching of *that* variable but does not order writes to *different* variables across cores or DMA. Multi-core/DMA hand-offs need real barriers (`__DMB()`), not just `volatile`.

---

## Firmware Lifecycle: Build, Flash, Boot

Firmware is C (sometimes C++/Rust) cross-compiled on your workstation for the target's architecture:

```bash
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -O2 -ffunction-sections \
  -T stm32f4.ld -nostartfiles startup.s app.c -o app.elf   # -T = linker script (the memory map)
arm-none-eabi-objcopy -O binary app.elf app.bin              # strip ELF to a raw flashable image
arm-none-eabi-size app.elf                                   # flash/SRAM usage vs the part's budget
```

The boot flow: on reset the CPU loads the initial stack pointer and the **reset vector** from the start of flash, runs startup code (init `.data`/`.bss`, set the clock tree), then `main()`. Many products boot a small **bootloader** first, which can accept a new image over a bus before jumping to the application — the basis of field/manufacturing update.

Getting an image onto the part, two routes:

- **Debug-port programming (JTAG/SWD)** — an external probe writes flash directly. This is the bring-up and factory path: deterministic, recoverable, and how you flash a virgin part that has no bootloader yet.
- **In-system / DFU** — the running firmware (or a resident bootloader) accepts an image over USB-DFU, UART, CAN, or the network. This is the field/line update path; it requires the device to already be bootable.

**Security and integrity** matter increasingly: **secure boot** has the bootloader verify a cryptographic signature on the image before running it (so only signed firmware executes), and **anti-rollback** prevents flashing an older, vulnerable version. At test you confirm signed images are accepted, unsigned/tampered ones are rejected, and the version meets the anti-rollback floor.

**Version control for firmware images.** Treat every firmware image as a released artifact: each shipped binary maps to a tagged commit, built reproducibly, with the version string baked into the image *and* readable at runtime. Tag releases (`fw-2.3.1`), never ship a binary you cannot rebuild from a tag, and record the firmware version in the per-unit results so you can trace which firmware touched each serial number. Version control of the *test programs* that flash and verify these images — branching, releasing, and rollback across stations — is covered in the Manufacturing Test chapter.

---

## Firmware Update in Manufacturing

Manufacturing frequently includes **flashing production firmware** as a test step: the board arrives with factory-default (or older) firmware, the station flashes the production image, then **verifies** it. Done wrong, you brick units at volume; done right, it is a clean staged operation with a rollback path. The canonical safe pattern has four phases — **stage → activate → verify → (rollback on failure)** — and **records the resulting version for traceability** every time.

### The staged (A/B) update model

Robust update mechanisms support **A/B (dual-bank) slots**: write the new image to the *inactive* slot (stage), switch the boot pointer and reset (activate), confirm it runs (verify), and if activation/verification fails, **roll back** by pointing at the still-intact previous slot. This is exactly how NVMe firmware slots and modern UEFI capsule / BMC redundant-image schemes work, and it is why a botched flash does not have to brick the unit.

```
        stage                 activate (reset)          verify              rollback
   +-----------+           +-----------------+      +-------------+      +-------------+
   | write new |           | switch boot ptr |      | read version|      | revert ptr  |
   | -> slot B | --------> | to slot B; reset| ---> | == expected?| -No->| to slot A   |
   | (slot A   |           | (slot A intact) |      | functional? |      | (good image)|
   |  stays    |           +-----------------+      +------+------+      +-------------+
   |  bootable)|                                            | Yes
   +-----------+                                            v
                                                     record version
                                                     in test results
```

### Commands by component

```
# --- GPU VBIOS ---
nvidia-smi --query-gpu=vbios_version --format=csv      # current VBIOS
# update via nvidia-smi / nvflash (NVIDIA proprietary) -- vendor-specific

# --- NVMe firmware (slot model: download -> activate -> verify) ---
nvme fw-log /dev/nvme0n1                                # slots + active firmware revision
nvme fw-download /dev/nvme0n1 --fw=prod_fw.bin          # STAGE into a slot
nvme fw-activate /dev/nvme0n1 --slot=2 --action=1       # ACTIVATE slot 2 (may need reset)
nvme fw-log /dev/nvme0n1                                # VERIFY active rev == expected
nvme smart-log /dev/nvme0n1                             # confirm healthy after activation

# --- BMC firmware (via IPMI) ---
ipmitool mc info                                       # current BMC firmware version
# update is vendor-specific (HPM.1 or vendor tool); BMCs often keep a redundant image

# --- BIOS/UEFI ---
dmidecode -t bios | grep Version                       # current BIOS version
# flashing via vendor flasher or a signed UEFI capsule update; verify version after
```

### The manufacturing firmware test pattern

1. **Read** the incoming version (record the *before* too).
2. **Stage** the production image (download to the inactive slot where supported).
3. **Activate** (reset if required).
4. **Verify the version matches expected** *and* **run a functional test** — a version string is necessary but not sufficient. Prove the device still enumerates, links, and passes its functional check on the new firmware.
5. **Roll back** to the known-good image if verify fails, and fail the unit cleanly with a decoded reason in the log.
6. **Record the final firmware version in the results** for traceability.

Robustness notes for an unattended CM line: **timeout every flash/activate/reset call** (a hung flasher must not wedge the station), never flash without a verified-good rollback path, and make the **failure state unambiguous** so a remote operator knows the unit failed and why.

---

## Debug and Bring-Up Tooling

The embedded debug kit, and what each is for:

- **JTAG / SWD** — the hardware debug port. SWD (Serial Wire Debug) is the 2-pin ARM variant; JTAG the older multi-pin standard that can also chain multiple devices. Through it you halt the core, read/write memory and registers, set breakpoints, and flash. This is ground truth when there is no console.
- **`gdb` + OpenOCD** — OpenOCD drives the JTAG/SWD probe and exposes a gdb server; you debug the MCU from `gdb` exactly as you would a Linux program (`target remote :3333`, `load`, `break`, `mon reset halt`). Same muscle memory as the Linux chapter's gdb.
- **UART console / semihosting** — a serial `printf` is the cheapest telemetry. Semihosting routes stdio through the debug probe when no UART is free, at the cost of halting the core per call (never leave it on in timed code).
- **Logic analyzer** — decodes I2C/SPI/UART traffic so you can see whether the bus transaction actually happened and matched the datasheet timing. Indispensable for "the device isn't responding" on a control bus.
- **Oscilloscope** — for anything analog or timing-level (rail ramp, signal integrity, the inrush and sequencing covered in the Power chapter).

The embedded bring-up reflex mirrors the board bring-up phases (Power chapter): confirm power and clocks before blaming firmware; confirm the bus transaction on a logic analyzer before blaming the device; read the fault status registers before guessing. Most "dead firmware" is a clock, a power rail, or a bus that never acked.

---

## ESD Awareness and Handling

Electrostatic discharge silently destroys — or, worse, *damages without killing* — semiconductor devices. A static event from a person can be thousands of volts: far below what you feel, far above what a sub-1 V gate oxide tolerates. For a test engineer the danger is twofold: a board you mishandle now, and — more insidiously — a **test fixture that makes you the failure mechanism**.

### The two failure modes (latent is the dangerous one)

- **Catastrophic** — the part dies immediately; the unit fails test. Bad, but you *catch* it.
- **Latent** — ESD weakens a junction or oxide without an immediate functional failure. The unit **passes test and ships**, then fails *in the field* weeks later. For a robotaxi this is the unacceptable case: a latent-damaged unit is a field/safety event waiting to happen, and your test never flagged it. This is precisely why ESD control is a *process* requirement, not a "be careful" suggestion.

### Standard controls (the ESD Protected Area)

- **Grounded wrist straps** on every operator (1 MΩ series resistor for safety), bonded to a common ground point.
- **Dissipative work surfaces** — conductive/dissipative mats, grounded.
- **ESD-safe packaging** in transit — shielding (Faraday) bags, dissipative trays; never bare boards in ordinary plastic (plastic generates charge).
- **Humidity control** — dry air builds far more static; EPAs control relative humidity.
- **ESD footwear/flooring and smocks**; ionizers where charge cannot be bled off directly (insulators).
- **Common-ground discipline** — DUT, fixture, instruments, mat, and person all bonded to the *same* ground, so there is no potential difference to discharge through the part.

### Why it matters specifically for test engineering

If your **fixture** lacks proper ESD grounding, the act of inserting or contacting the DUT can zap it. So ensure fixture contacts and probes are ESD-safe and grounded, that the DUT shares the station ground *before* signal pins mate, and that **wrist-strap monitors are verified working on every station, every shift** — a strap with a broken cord gives false confidence, so a continuous monitor beats a once-a-day check. An ESD escape is invisible at test and catastrophic in the field; the process controls are the safety net, and verifying them is part of keeping the line honest.

---

## Low-Speed Control Buses (Cross-Reference)

The embedded world runs on three low-speed buses — **I2C** (multi-drop, 2-wire, for configuration and telemetry), **SPI** (fast, point-to-point, for flash and ADCs), and **UART** (the debug console and simple links). On a compute board they configure cameras and deserializers, read PMIC telemetry, and carry the console. From the embedded side they are just register read/write sequences over a bus: an I2C device exposes registers you address and read/write, often with a documented power-on init sequence the firmware must replay.

Their protocol depth — addressing, arbitration, clock stretching, modes, and the test-floor patterns for each — is in the **Automotive and Serial Buses chapter**; their use inside fixture and instrument control is in the **Python chapter** (pyserial, the `equipment_rpc` socket pattern). This chapter's point is only that, on an MCU, a bus transaction is a register operation you can see on a logic analyzer and must match to the datasheet.

---

## Throughlines

- The AV compute platform is an embedded system; the MCUs around the SoCs (BMC, safety supervisor, sequencers, sensor hubs) come up first and own the behavior that `dmesg` never sees.
- Embedded is the register skill you already have from PCIe, one level lower and without a driver in the way — with `volatile`, read-modify-write races, and write-1-to-clear as the recurring hazards.
- Real-time means *deterministic*, not fast: reason about jitter, WCET, priority inversion, and the watchdog/brown-out mechanisms that make a board fail safe.
- Firmware is a lifecycle — build reproducibly, flash recoverably (JTAG/SWD for virgin parts, A/B staged update for the line), verify functionally not just by version string, and record the version for traceability.
- ESD control is a process, not a caution: latent damage ships and fails in the field, so verifying the EPA and the fixture grounding is part of the test engineer's job.
