/*
 * pcie_bert_core — the testable core of the PCIe error counter.
 *
 * This header exposes the pure logic and the config-space access *seam* so it can
 * be unit-tested off-hardware (see c/test/). The production program (pcie_bert.c)
 * owns main(), the sysfs file reads, and the real pread/pwrite backend; everything
 * here is hardware-agnostic and driven through the `cfg_io` seam.
 *
 * The seam: config-space byte access goes through two function pointers whose
 * signatures deliberately mirror pread/pwrite, so the production backend is a thin
 * forward (fd_read/fd_write in pcie_bert.c) and a unit test can swap in an in-memory
 * register model with true write-1-to-clear (c/test/fake_cfgspace.c). Endianness and
 * the 1/2/4-byte assembly live ONE level up (cfg_read/cfg_write), so a backend only
 * moves bytes.
 */
#ifndef PCIE_BERT_CORE_H
#define PCIE_BERT_CORE_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>   /* off_t, ssize_t */

/* --- The config-space I/O seam ------------------------------------------------ */
typedef ssize_t (*reg_read_fn)(void *ctx, void *buf, size_t n, off_t off);
typedef ssize_t (*reg_write_fn)(void *ctx, const void *buf, size_t n, off_t off);

typedef struct {
    reg_read_fn  read;
    reg_write_fn write;
    void        *ctx;       /* backend state: an int* fd in prod, a model* in tests */
} cfg_io;

/* --- Register offsets / capability IDs / status bits -------------------------- */
/* AER extended-cap register offsets (relative to the AER base). */
enum { AER_UNCORR_STATUS = 0x04, AER_CORR_STATUS = 0x10 };
enum { ECAP_AER = 0x0001 };

/* Legacy PCIe cap + Device Status (the no-AER fallback error source). */
enum { CAP_PCIE = 0x10, PCIE_DEV_STATUS = 0x0A };
enum { DEVSTA_CORR = 1u << 0, DEVSTA_NONFATAL = 1u << 1, DEVSTA_FATAL = 1u << 2 };

/* Named correctable bits per source (see computetest/aer.py for the full AER map). */
struct cor_bit { int bit; const char *name; };
extern const struct cor_bit AER_COR_BITS[];
extern const struct cor_bit DEVSTA_COR_BITS[];
#define N_AER_COR_BITS    8
#define N_DEVSTA_COR_BITS 1

/* --- Config-space access over the seam (little-endian; n in 1..4) ------------- */
int cfg_read(const cfg_io *io, off_t off, int n, uint32_t *out);
int cfg_write(const cfg_io *io, off_t off, int n, uint32_t v);

/* --- Capability-list walks ---------------------------------------------------- */
off_t find_ext_cap(const cfg_io *io, uint16_t cap_id);  /* extended list from 0x100 */
off_t find_cap(const cfg_io *io, uint8_t cap_id);        /* legacy list from 0x34 */

/* --- The write-1-to-clear primitive ------------------------------------------- */
/* Read a status register and write-1-to-clear the set bits in `mask`. `width` is the
 * register width in bytes (4 for the 32-bit AER status registers, 2 for the 16-bit
 * Device Status register) — writing the register's own width keeps the W1C from
 * touching the adjacent register. Returns the set (masked) bits. */
uint32_t read_and_clear(const cfg_io *io, off_t reg, uint32_t mask, int width);

/* --- Per-iteration accounting (extracted from the hot loop so it's testable) -- */
void account_correctable(uint32_t cs, const struct cor_bit *tbl, int n,
                         long cor_events[], long *cor_total);
/* Count each uncorrectable *type* once across the run: a latch persists, so a bit
 * already seen must not be re-counted. */
void account_uncorrectable(uint32_t us, uint32_t *unc_seen, long *unc_total);

/* --- Pure helpers ------------------------------------------------------------- */
double link_bits_per_sec(int code, int width);
int    speed_code_from_str(const char *s);  /* "16.0 GT/s" -> 4; 0 if unknown */
int    valid_bdf(const char *s);            /* strict DDDD:BB:DD.F (func 0-7) */

#endif /* PCIE_BERT_CORE_H */
