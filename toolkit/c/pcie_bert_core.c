/*
 * pcie_bert_core — implementations of the testable core. See pcie_bert_core.h.
 * No I/O of its own: all config-space access goes through the cfg_io seam.
 */
#include "pcie_bert_core.h"

#include <stdio.h>     /* sscanf */
#include <stdlib.h>    /* atof */

const struct cor_bit AER_COR_BITS[] = {
    {0, "RxErr"}, {6, "BadTLP"}, {7, "BadDLLP"}, {8, "RollOver"},
    {12, "ReplayTO"}, {13, "AdvNonFatal"}, {14, "CorrIntErr"}, {15, "HdrLogOvf"},
};
const struct cor_bit DEVSTA_COR_BITS[] = { {0, "CorrErrDetected"} };

/* Read `n` bytes of config space at `off` into a uint32 (little-endian). */
int cfg_read(const cfg_io *io, off_t off, int n, uint32_t *out) {
    uint8_t buf[4] = {0};
    if (io->read(io->ctx, buf, (size_t)n, off) != n) return -1;
    uint32_t v = 0;
    for (int i = 0; i < n; i++) v |= (uint32_t)buf[i] << (8 * i);
    *out = v;
    return 0;
}

/* Write `n` bytes (little-endian) of config space at `off`. */
int cfg_write(const cfg_io *io, off_t off, int n, uint32_t v) {
    uint8_t buf[4];
    for (int i = 0; i < n; i++) buf[i] = (uint8_t)((v >> (8 * i)) & 0xff);
    return (io->write(io->ctx, buf, (size_t)n, off) == n) ? 0 : -1;
}

/* Walk the extended-capability linked list (from 0x100) to find a capability by ID. */
off_t find_ext_cap(const cfg_io *io, uint16_t cap_id) {
    off_t off = 0x100;
    for (int guard = 0; guard < 64 && off; guard++) {
        uint32_t hdr;
        if (cfg_read(io, off, 4, &hdr) != 0) return 0;
        if (hdr == 0 || hdr == 0xffffffffu) return 0;
        if ((hdr & 0xffff) == cap_id) return off;
        off = (hdr >> 20) & 0xfff;  /* next-capability offset */
    }
    return 0;
}

/* Walk the legacy capability list (from the 0x34 pointer) for a capability ID. */
off_t find_cap(const cfg_io *io, uint8_t cap_id) {
    uint32_t p = 0;
    if (cfg_read(io, 0x34, 1, &p) != 0) return 0;
    off_t ptr = p & 0xFC;
    for (int guard = 0; guard < 48 && ptr; guard++) {
        uint32_t id = 0, nxt = 0;
        if (cfg_read(io, ptr, 1, &id) != 0) return 0;
        if ((id & 0xff) == cap_id) return ptr;
        if (cfg_read(io, ptr + 1, 1, &nxt) != 0) return 0;
        ptr = nxt & 0xFC;
    }
    return 0;
}

/* Read a status register and write-1-to-clear the bits in `mask` that are set.
 * Writing the register's own `width` (2 for Device Status at PCIe-cap+0x0A, 4 for
 * the AER status registers) keeps the W1C from spanning into the adjacent register
 * (Link Capabilities sits at PCIe-cap+0x0C). Returns the set (masked) bits. */
uint32_t read_and_clear(const cfg_io *io, off_t reg, uint32_t mask, int width) {
    uint32_t s = 0;
    if (cfg_read(io, reg, width, &s) != 0) return 0;
    s &= mask;
    if (s) cfg_write(io, reg, width, s);   /* W1C only the bits we handle */
    return s;
}

void account_correctable(uint32_t cs, const struct cor_bit *tbl, int n,
                         long cor_events[], long *cor_total) {
    for (int i = 0; i < n; i++)
        if (cs & (1u << tbl[i].bit)) { cor_events[i]++; (*cor_total)++; }
}

void account_uncorrectable(uint32_t us, uint32_t *unc_seen, long *unc_total) {
    *unc_total += __builtin_popcount(us & ~*unc_seen);   /* count each type once */
    *unc_seen |= us;
}

double link_bits_per_sec(int code, int width) {
    static const double gtps[] = {0, 2.5e9, 5e9, 8e9, 16e9, 32e9, 64e9};
    double eff = (code <= 2) ? 0.8 : (code <= 5 ? 128.0 / 130.0 : 242.0 / 256.0);
    if (code < 1 || code > 6) return 0;
    return gtps[code] * (double)width * eff;
}

/* Read e.g. "16.0 GT/s" -> speed code; returns 0 if unknown. */
int speed_code_from_str(const char *s) {
    double g = atof(s);
    if (g >= 63) return 6;
    if (g >= 31) return 5;
    if (g >= 15) return 4;
    if (g >= 7.5) return 3;
    if (g >= 4.5) return 2;
    if (g >= 2) return 1;
    return 0;
}

/* Strict BDF grammar DDDD:BB:DD.F (hex; function 0-7). Rejects junk and path
 * traversal before the value reaches snprintf()/JSON. */
int valid_bdf(const char *s) {
    int n = 0;
    sscanf(s, "%*4[0-9a-fA-F]:%*2[0-9a-fA-F]:%*2[0-9a-fA-F].%*1[0-7]%n", &n);
    return n == 12 && s[n] == '\0';
}
