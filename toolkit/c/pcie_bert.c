/*
 * pcie_bert — exercise/monitor a PCIe link and count AER errors over a window.
 *
 * The C half of the BERT (the Python half, computetest/ber.py, turns the counts
 * into a confidence level). This program:
 *   1. Finds the AER extended capability for a BDF (walks the ext-cap list).
 *   2. Arms the link: write-1-to-clear ONLY the set AER status bits, then verify.
 *   3. For the test duration, polls the correctable/uncorrectable status as fast
 *      as it can; the instant a bit latches it records the event and re-clears it
 *      (W1C) so the next error can latch. (A latch is "an error happened", not a
 *      count — clearing fast is how you actually count, and why slow clearing
 *      undercounts. This is the requirement the tool is built around.)
 *   4. Accounts bits transferred from link_speed x link_width x elapsed time.
 *   5. Prints a JSON record for the orchestrator to consume.
 *
 * Linux only (reads /sys/bus/pci). Config-space writes (the W1C clear) need root.
 *
 * Build:  cc -O2 -Wall -Wextra -std=c11 -o pcie_bert pcie_bert.c
 * Usage:  sudo ./pcie_bert -d 0000:03:00.0 -t 12 --json
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

/* AER register offsets relative to the AER capability base. */
enum { AER_UNCORR_STATUS = 0x04, AER_CORR_STATUS = 0x10 };
enum { ECAP_AER = 0x0001 };

/* Correctable status bit positions we name (see computetest/aer.py for full map). */
static const struct { int bit; const char *name; } COR_BITS[] = {
    {0, "RxErr"}, {6, "BadTLP"}, {7, "BadDLLP"}, {8, "RollOver"},
    {12, "ReplayTO"}, {13, "AdvNonFatal"}, {14, "CorrIntErr"}, {15, "HdrLogOvf"},
};
#define N_COR_BITS ((int)(sizeof(COR_BITS) / sizeof(COR_BITS[0])))

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

/* W1C: read status; if non-zero, write back exactly the set bits; verify.
 * Returns bits that were set (the "events" seen); sets *stuck to any that
 * refused to clear (a persistent/sticky hardware error worth reporting). */
static uint32_t clear_set_bits(int fd, off_t reg, uint32_t *stuck) {
    uint32_t s = 0;
    if (cfg_read(fd, reg, 4, &s) != 0) { *stuck = 0; return 0; }
    if (s == 0) { *stuck = 0; return 0; }
    if (cfg_write(fd, reg, 4, s) != 0) {   /* write failed (e.g. EACCES) -> can't clear */
        *stuck = s; return s;              /* treat all set bits as stuck */
    }
    uint32_t after = 0;
    if (cfg_read(fd, reg, 4, &after) != 0) { *stuck = 0; return s; }
    *stuck = after & s;                    /* bits that refused to clear */
    return s;
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

    off_t aer = find_ext_cap(fd, ECAP_AER);
    if (!aer) { fprintf(stderr, "error: no AER capability on %s\n", bdf); close(fd); return 1; }

    int code = read_link_speed_code(bdf), width = read_link_width(bdf);
    double bps = link_bits_per_sec(code, width);

    /* Arm: clear both status latches before measuring. */
    uint32_t stuck_c = 0, stuck_u = 0;
    clear_set_bits(fd, aer + AER_CORR_STATUS, &stuck_c);
    clear_set_bits(fd, aer + AER_UNCORR_STATUS, &stuck_u);

    long cor_events[N_COR_BITS] = {0};
    long cor_total = 0, unc_total = 0;
    uint32_t unc_seen = 0;
    /* Once a bit refuses to clear it is "stuck": count it once, then ignore it so a
     * sticky hardware fault cannot inflate the count into the millions (A-P0-1). A
     * genuinely recurring error clears each poll and is recounted; a stuck one isn't. */
    uint32_t cor_stuck = stuck_c, unc_stuck = stuck_u;

    double t0 = now_sec();
    while (now_sec() - t0 < duration) {
        uint32_t st = 0;
        uint32_t set = clear_set_bits(fd, aer + AER_CORR_STATUS, &st);  /* read+clear */
        uint32_t newly = set & ~cor_stuck;     /* exclude already-known-stuck bits */
        if (newly) {
            cor_total++;                        /* one clear-recount EVENT */
            for (int i = 0; i < N_COR_BITS; i++)
                if (newly & (1u << COR_BITS[i].bit)) cor_events[i]++;
        }
        cor_stuck |= st;

        uint32_t su = 0;
        uint32_t setu = clear_set_bits(fd, aer + AER_UNCORR_STATUS, &su);
        if (setu & ~unc_seen) unc_total++;      /* count each uncorrectable type once */
        unc_seen |= setu;
        unc_stuck |= su;
        /* No sleep: poll fast so distinct error events latch separately, not merged. */
    }
    double elapsed = now_sec() - t0;
    double bits = bps * elapsed;
    int link_unknown = (bps <= 0);              /* couldn't read link speed/width */
    if (link_unknown)
        fprintf(stderr, "warning: link speed/width unknown for %s; bits=0\n", bdf);
    close(fd);

    if (json) {
        printf("{\"bdf\":\"%s\",\"seconds\":%.3f,\"link_speed_code\":%d,"
               "\"link_width\":%d,\"link_unknown\":%s,\"bits\":%.6e,\"correctable\":%ld,"
               "\"uncorrectable\":%ld,\"uncorrectable_bits\":%u,"
               "\"stuck_correctable\":%u,\"stuck_uncorrectable\":%u,"
               "\"per_correctable\":{",
               bdf, elapsed, code, width, link_unknown ? "true" : "false",
               bits, cor_total, unc_total, unc_seen, cor_stuck, unc_stuck);
        int first = 1;
        for (int i = 0; i < N_COR_BITS; i++) {
            if (cor_events[i]) {
                printf("%s\"%s\":%ld", first ? "" : ",", COR_BITS[i].name, cor_events[i]);
                first = 0;
            }
        }
        printf("}}\n");
    } else {
        printf("BDF %s: %.3fs, Gen%d x%d, bits=%.3e, correctable=%ld, uncorrectable=%ld\n",
               bdf, elapsed, code, width, bits, cor_total, unc_total);
    }
    return (unc_total > 0) ? 3 : 0;  /* nonzero exit if any uncorrectable error */
}
