/*
 * pcie_bert — the dumb, fast PCIe error counter.
 *
 * This program has exactly ONE job: for the number of seconds Python hands it,
 * count PCIe errors as fast and as accurately as possible, then print the totals
 * as JSON. It decides nothing else. The Python conductor (computetest/bert.py)
 * owns everything that is NOT speed-critical: how long to run (and whether to
 * extend), the idle baseline that distinguishes a stuck bit from a high error
 * rate, link-downgrade monitoring, and the confidence math. Keeping this loop
 * minimal is the point — every microsecond of loop latency merges error latches
 * and undercounts.
 *
 * What it does:
 *   1. Pick the error source: AER (per-type, preferred) else the Device Status
 *      register (coarse correctable/uncorrectable, present on every PCIe function).
 *   2. Arm: write-1-to-clear the handled status bits.
 *   3. Tight loop for the given duration: poll CORRECTABLE every iteration (read,
 *      count each set bit, W1C-clear) and sample UNCORRECTABLE periodically (its
 *      latch persists, so this loses nothing and keeps the hot path one config read
 *      per iteration). A latch means ">=1 error of that type"; counting set bits per
 *      poll is the tightest count the registers allow.
 *   4. Account bits from link_speed x link_width x elapsed time.
 *   5. Print JSON: {source, link_speed_code, link_width, link_unknown, bits,
 *      correctable, uncorrectable, uncorrectable_bits, per_correctable}.
 *
 * The hardware-agnostic logic (config-space access, capability walks, the W1C
 * primitive, the accounting, the parsers) lives in pcie_bert_core.{c,h} behind a
 * cfg_io seam so it can be unit-tested off-hardware; this file supplies main(), the
 * sysfs file reads, and the real pread/pwrite backend.
 *
 * Linux only (reads /sys/bus/pci). Config-space writes (the W1C clear) need root.
 * The sysfs root is overridable via -r <dir> or $COMPUTETEST_SYSROOT (for off-hardware
 * testing against a fixture/FUSE/QEMU-guest tree); it defaults to /sys/bus/pci/devices.
 *
 * Build:  cc -O2 -Wall -Wextra -std=c11 -o pcie_bert pcie_bert.c pcie_bert_core.c
 * Usage:  ./pcie_bert -d 0000:03:00.0 -t 12 [-r <sysfs-root>] --json
 */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

#include "pcie_bert_core.h"

#define SYS_PCI "/sys/bus/pci/devices"

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* Production config-space backend: pread/pwrite on the open .../config fd. The
 * signatures mirror pread/pwrite so these are thin forwards; the cfg_io seam lets
 * unit tests swap in an in-memory register model (see c/test/fake_cfgspace.c). */
static ssize_t fd_read(void *ctx, void *buf, size_t n, off_t off) {
    return pread(*(int *)ctx, buf, n, off);
}
static ssize_t fd_write(void *ctx, const void *buf, size_t n, off_t off) {
    return pwrite(*(int *)ctx, buf, n, off);
}

/* Read e.g. "16.0 GT/s" from sysfs -> speed code; 0 if unreadable/unknown. */
static int read_link_speed_code(const char *sys_root, const char *bdf) {
    char path[512], buf[64] = {0};
    snprintf(path, sizeof path, "%s/%s/current_link_speed", sys_root, bdf);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    int ok = (fgets(buf, sizeof buf, f) != NULL);
    fclose(f);
    return ok ? speed_code_from_str(buf) : 0;
}

static int read_link_width(const char *sys_root, const char *bdf) {
    char path[512], buf[64] = {0};
    snprintf(path, sizeof path, "%s/%s/current_link_width", sys_root, bdf);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    int w = (fgets(buf, sizeof buf, f)) ? atoi(buf) : 0;
    fclose(f);
    return w;
}

int main(int argc, char **argv) {
    const char *bdf = NULL;
    double duration = 12.0;
    int json = 0;
    const char *sys_root = getenv("COMPUTETEST_SYSROOT");
    if (!sys_root) sys_root = SYS_PCI;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) {
            bdf = argv[++i];
        } else if (!strcmp(argv[i], "-t") && i + 1 < argc) {
            char *end = NULL;
            duration = strtod(argv[++i], &end);
            if (end == argv[i] || *end != '\0' || duration <= 0 || !isfinite(duration)) {
                fprintf(stderr, "error: -t needs a positive, finite number of seconds\n");
                return 2;
            }
        } else if (!strcmp(argv[i], "-r") && i + 1 < argc) {
            sys_root = argv[++i];          /* sysfs root (default $COMPUTETEST_SYSROOT or /sys) */
        } else if (!strcmp(argv[i], "--json")) {
            json = 1;
        } else {
            fprintf(stderr, "usage: %s -d <bdf> -t <sec> [-r <sysfs-root>] [--json]\n", argv[0]);
            return 2;
        }
    }
    if (!bdf) { fprintf(stderr, "error: -d <bdf> required\n"); return 2; }
    if (!valid_bdf(bdf)) {
        fprintf(stderr, "error: malformed BDF '%s' (expected e.g. 0000:03:00.0)\n", bdf);
        return 2;
    }

    char cfgpath[512];
    snprintf(cfgpath, sizeof cfgpath, "%s/%s/config", sys_root, bdf);
    int fd = open(cfgpath, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "error: open %s: %s (need root for config writes)\n",
                cfgpath, strerror(errno));
        return 1;
    }
    cfg_io io = { fd_read, fd_write, &fd };

    /* Pick the error source: AER (per-type, preferred) else Device Status (coarse). */
    off_t aer = find_ext_cap(&io, ECAP_AER);
    off_t pcie_cap = find_cap(&io, CAP_PCIE);
    const char *source;
    off_t cor_off, unc_off;
    uint32_t cor_mask, unc_mask;
    int reg_width;                                    /* status-register width in bytes */
    const struct cor_bit *cor_tbl;
    int n_cor;
    if (aer) {
        source = "aer";
        cor_off = aer + AER_CORR_STATUS;
        unc_off = aer + AER_UNCORR_STATUS;
        cor_mask = 0xFFFFFFFFu;
        unc_mask = 0xFFFFFFFFu;
        reg_width = 4;                                /* 32-bit AER status registers */
        cor_tbl = AER_COR_BITS;
        n_cor = N_AER_COR_BITS;
    } else if (pcie_cap) {
        source = "devstatus";
        cor_off = unc_off = pcie_cap + PCIE_DEV_STATUS;
        cor_mask = DEVSTA_CORR;                       /* bit 0 */
        unc_mask = DEVSTA_NONFATAL | DEVSTA_FATAL;    /* bits 1, 2 */
        reg_width = 2;                                /* 16-bit Device Status register */
        cor_tbl = DEVSTA_COR_BITS;
        n_cor = N_DEVSTA_COR_BITS;
    } else {
        fprintf(stderr, "error: no AER or PCIe capability on %s\n", bdf);
        close(fd);
        return 1;
    }

    int code = read_link_speed_code(sys_root, bdf), width = read_link_width(sys_root, bdf);
    double bps = link_bits_per_sec(code, width);

    /* Arm: clear the handled status bits before counting. */
    read_and_clear(&io, cor_off, cor_mask, reg_width);
    read_and_clear(&io, unc_off, unc_mask, reg_width);

    long cor_events[16] = {0};
    long cor_total = 0, unc_total = 0;
    uint32_t unc_seen = 0;

    /* The tight loop. The bottleneck is the config-space read (a real PCIe config
     * transaction through the kernel, ~us; config space can't be mmap'd), so we
     * minimize transactions per iteration: poll CORRECTABLE every iteration (that's
     * the count the BER needs), and sample UNCORRECTABLE only every UNC_EVERY
     * iterations. Uncorrectable is a rare, binary fail-flag whose latch persists
     * until read, so periodic sampling loses no information — it just halves the
     * hot-path config reads, doubling the correctable poll rate (tighter clear
     * windows => fewer merged latches => more accurate counting where it matters).
     * No sleep, no stuck logic, no idle baseline, no confidence — Python owns those. */
    const long UNC_EVERY = 64;
    long iters = 0;
    double t0 = now_sec();
    while (now_sec() - t0 < duration) {
        uint32_t cs = read_and_clear(&io, cor_off, cor_mask, reg_width); /* hot path: 1 read when clean */
        account_correctable(cs, cor_tbl, n_cor, cor_events, &cor_total);
        if (++iters % UNC_EVERY == 0) {
            uint32_t us = read_and_clear(&io, unc_off, unc_mask, reg_width);
            account_uncorrectable(us, &unc_seen, &unc_total);
        }
    }
    /* Final uncorrectable sample — the latch persists, so this catches anything since
     * the last periodic check (and handles very short runs that never hit UNC_EVERY). */
    {
        uint32_t us = read_and_clear(&io, unc_off, unc_mask, reg_width);
        account_uncorrectable(us, &unc_seen, &unc_total);
    }
    double elapsed = now_sec() - t0;
    double bits = bps * elapsed;
    int link_unknown = (bps <= 0);              /* couldn't read link speed/width */
    if (link_unknown)
        fprintf(stderr, "warning: link speed/width unknown for %s; bits=0\n", bdf);
    close(fd);

    if (json) {
        printf("{\"bdf\":\"%s\",\"seconds\":%.3f,\"source\":\"%s\",\"link_speed_code\":%d,"
               "\"link_width\":%d,\"link_unknown\":%s,\"bits\":%.6e,\"correctable\":%ld,"
               "\"uncorrectable\":%ld,\"uncorrectable_bits\":%u,\"per_correctable\":{",
               bdf, elapsed, source, code, width, link_unknown ? "true" : "false",
               bits, cor_total, unc_total, unc_seen);
        int first = 1;
        for (int i = 0; i < n_cor; i++) {
            if (cor_events[i]) {
                printf("%s\"%s\":%ld", first ? "" : ",", cor_tbl[i].name, cor_events[i]);
                first = 0;
            }
        }
        printf("}}\n");
    } else {
        printf("BDF %s: %.3fs, %s, Gen%d x%d, bits=%.3e, correctable=%ld, uncorrectable=%ld\n",
               bdf, elapsed, source, code, width, bits, cor_total, unc_total);
    }
    return (unc_total > 0) ? 3 : 0;  /* nonzero exit if any uncorrectable error */
}
