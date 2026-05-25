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
 * Linux only (reads /sys/bus/pci). Config-space writes (the W1C clear) need root.
 *
 * Build:  cc -O2 -Wall -Wextra -std=c11 -o pcie_bert pcie_bert.c
 * Usage:  ./pcie_bert -d 0000:03:00.0 -t 12 --json   (Python normally calls this)
 */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

#define SYS_PCI "/sys/bus/pci/devices"

/* AER extended-cap register offsets (relative to the AER base). */
enum { AER_UNCORR_STATUS = 0x04, AER_CORR_STATUS = 0x10 };
enum { ECAP_AER = 0x0001 };

/* Legacy PCIe cap + Device Status (the no-AER fallback error source). */
enum { CAP_PCIE = 0x10, PCIE_DEV_STATUS = 0x0A };
enum { DEVSTA_CORR = 1u << 0, DEVSTA_NONFATAL = 1u << 1, DEVSTA_FATAL = 1u << 2 };

/* Named correctable bits per source (see computetest/aer.py for the full AER map). */
struct cor_bit { int bit; const char *name; };
static const struct cor_bit AER_COR_BITS[] = {
    {0, "RxErr"}, {6, "BadTLP"}, {7, "BadDLLP"}, {8, "RollOver"},
    {12, "ReplayTO"}, {13, "AdvNonFatal"}, {14, "CorrIntErr"}, {15, "HdrLogOvf"},
};
static const struct cor_bit DEVSTA_COR_BITS[] = { {0, "CorrErrDetected"} };

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* Read `n` bytes of config space at `off` into a uint32 (little-endian). */
static int cfg_read(int fd, off_t off, int n, uint32_t *out) {
    uint8_t buf[4] = {0};
    ssize_t r = pread(fd, buf, n, off);
    if (r != n) return -1;
    uint32_t v = 0;
    for (int i = 0; i < n; i++) v |= (uint32_t)buf[i] << (8 * i);
    *out = v;
    return 0;
}

/* Write `n` bytes (little-endian) of config space at `off`. Needs root. */
static int cfg_write(int fd, off_t off, int n, uint32_t v) {
    uint8_t buf[4];
    for (int i = 0; i < n; i++) buf[i] = (v >> (8 * i)) & 0xff;
    ssize_t w = pwrite(fd, buf, n, off);
    return (w == n) ? 0 : -1;
}

/* Walk the extended-capability linked list to find a capability by ID. */
static off_t find_ext_cap(int fd, uint16_t cap_id) {
    off_t off = 0x100;
    for (int guard = 0; guard < 64 && off; guard++) {
        uint32_t hdr;
        if (cfg_read(fd, off, 4, &hdr) != 0) return 0;
        if (hdr == 0 || hdr == 0xffffffffu) return 0;
        if ((hdr & 0xffff) == cap_id) return off;
        off = (hdr >> 20) & 0xfff;  /* next-capability offset */
    }
    return 0;
}

/* Read a status register and write-1-to-clear the bits in `mask` that are set.
 * Returns the set (masked) bits. Stuck-bit interpretation is the Python conductor's
 * job (via an idle baseline) — this counter just reads, counts, and clears, fast. */
static uint32_t read_and_clear(int fd, off_t reg, uint32_t mask) {
    uint32_t s = 0;
    if (cfg_read(fd, reg, 4, &s) != 0) return 0;
    s &= mask;
    if (s) cfg_write(fd, reg, 4, s);       /* W1C only the bits we handle */
    return s;
}

/* Walk the legacy capability list (from the 0x34 pointer) for a capability ID. */
static off_t find_cap(int fd, uint8_t cap_id) {
    uint32_t p = 0;
    if (cfg_read(fd, 0x34, 1, &p) != 0) return 0;
    off_t ptr = p & 0xFC;
    for (int guard = 0; guard < 48 && ptr; guard++) {
        uint32_t id = 0, nxt = 0;
        if (cfg_read(fd, ptr, 1, &id) != 0) return 0;
        if ((id & 0xff) == cap_id) return ptr;
        if (cfg_read(fd, ptr + 1, 1, &nxt) != 0) return 0;
        ptr = nxt & 0xFC;
    }
    return 0;
}

/* Read e.g. "16.0 GT/s" -> speed code; returns 0 if unknown. */
static int read_link_speed_code(const char *bdf) {
    char path[256], buf[64] = {0};
    snprintf(path, sizeof path, SYS_PCI "/%s/current_link_speed", bdf);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    if (!fgets(buf, sizeof buf, f)) { fclose(f); return 0; }
    fclose(f);
    double g = atof(buf);
    if (g >= 63) return 6; if (g >= 31) return 5; if (g >= 15) return 4;
    if (g >= 7.5) return 3; if (g >= 4.5) return 2; if (g >= 2) return 1;
    return 0;
}

static int read_link_width(const char *bdf) {
    char path[256], buf[64] = {0};
    snprintf(path, sizeof path, SYS_PCI "/%s/current_link_width", bdf);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    int w = (fgets(buf, sizeof buf, f)) ? atoi(buf) : 0;
    fclose(f);
    return w;
}

static double link_bits_per_sec(int code, int width) {
    static const double gtps[] = {0, 2.5e9, 5e9, 8e9, 16e9, 32e9, 64e9};
    double eff = (code <= 2) ? 0.8 : (code <= 5 ? 128.0 / 130.0 : 242.0 / 256.0);
    if (code < 1 || code > 6) return 0;
    return gtps[code] * (double)width * eff;
}

/* Strict BDF grammar DDDD:BB:DD.F (hex; function 0-7). Rejects junk and path
 * traversal before the value reaches snprintf()/JSON. */
static int valid_bdf(const char *s) {
    int n = 0;
    sscanf(s, "%*4[0-9a-fA-F]:%*2[0-9a-fA-F]:%*2[0-9a-fA-F].%*1[0-7]%n", &n);
    return n == 12 && s[n] == '\0';
}

int main(int argc, char **argv) {
    const char *bdf = NULL;
    double duration = 12.0;
    int json = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) {
            bdf = argv[++i];
        } else if (!strcmp(argv[i], "-t") && i + 1 < argc) {
            char *end = NULL;
            duration = strtod(argv[++i], &end);
            if (end == argv[i] || *end != '\0' || duration <= 0) {
                fprintf(stderr, "error: -t needs a positive number of seconds\n");
                return 2;
            }
        } else if (!strcmp(argv[i], "--json")) {
            json = 1;
        } else {
            fprintf(stderr, "usage: %s -d <bdf> -t <sec> [--json]\n", argv[0]);
            return 2;
        }
    }
    if (!bdf) { fprintf(stderr, "error: -d <bdf> required\n"); return 2; }
    if (!valid_bdf(bdf)) {
        fprintf(stderr, "error: malformed BDF '%s' (expected e.g. 0000:03:00.0)\n", bdf);
        return 2;
    }

    char cfgpath[256];
    snprintf(cfgpath, sizeof cfgpath, SYS_PCI "/%s/config", bdf);
    int fd = open(cfgpath, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "error: open %s: %s (need root for config writes)\n",
                cfgpath, strerror(errno));
        return 1;
    }

    /* Pick the error source: AER (per-type, preferred) else Device Status (coarse). */
    off_t aer = find_ext_cap(fd, ECAP_AER);
    off_t pcie_cap = find_cap(fd, CAP_PCIE);
    const char *source;
    off_t cor_off, unc_off;
    uint32_t cor_mask, unc_mask;
    const struct cor_bit *cor_tbl;
    int n_cor;
    if (aer) {
        source = "aer";
        cor_off = aer + AER_CORR_STATUS;
        unc_off = aer + AER_UNCORR_STATUS;
        cor_mask = 0xFFFFFFFFu;
        unc_mask = 0xFFFFFFFFu;
        cor_tbl = AER_COR_BITS;
        n_cor = (int)(sizeof AER_COR_BITS / sizeof AER_COR_BITS[0]);
    } else if (pcie_cap) {
        source = "devstatus";
        cor_off = unc_off = pcie_cap + PCIE_DEV_STATUS;
        cor_mask = DEVSTA_CORR;                       /* bit 0 */
        unc_mask = DEVSTA_NONFATAL | DEVSTA_FATAL;    /* bits 1, 2 */
        cor_tbl = DEVSTA_COR_BITS;
        n_cor = (int)(sizeof DEVSTA_COR_BITS / sizeof DEVSTA_COR_BITS[0]);
    } else {
        fprintf(stderr, "error: no AER or PCIe capability on %s\n", bdf);
        close(fd);
        return 1;
    }

    int code = read_link_speed_code(bdf), width = read_link_width(bdf);
    double bps = link_bits_per_sec(code, width);

    /* Arm: clear the handled status bits before counting. */
    read_and_clear(fd, cor_off, cor_mask);
    read_and_clear(fd, unc_off, unc_mask);

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
        uint32_t cs = read_and_clear(fd, cor_off, cor_mask);   /* hot path: 1 read when clean */
        for (int i = 0; i < n_cor; i++)
            if (cs & (1u << cor_tbl[i].bit)) { cor_events[i]++; cor_total++; }
        if (++iters % UNC_EVERY == 0) {
            uint32_t us = read_and_clear(fd, unc_off, unc_mask);
            unc_total += __builtin_popcount(us & ~unc_seen);   /* count each type once */
            unc_seen |= us;
        }
    }
    /* Final uncorrectable sample — the latch persists, so this catches anything since
     * the last periodic check (and handles very short runs that never hit UNC_EVERY). */
    {
        uint32_t us = read_and_clear(fd, unc_off, unc_mask);
        unc_total += __builtin_popcount(us & ~unc_seen);
        unc_seen |= us;
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
