/*
 * fake_cfgspace — an in-memory PCIe config-space model for unit tests.
 *
 * Implements the cfg_io seam (fake_read/fake_write) against a 4 KB byte array with
 * *true* write-1-to-clear semantics on registers you mark as W1C, plus per-bit
 * "stuck" bits that refuse to clear (to exercise stuck-bit handling). This lets the
 * pcie_bert_core W1C loop and capability walks run with zero hardware and full
 * determinism.
 *
 * W1C is modeled per byte: a write to a byte marked W1C clears exactly the 1-bits
 * written (minus any stuck bits); a write to a normal byte stores it. So a 2-byte
 * write to a 2-byte W1C register touches only those 2 bytes, while a (buggy) 4-byte
 * write would also overwrite the adjacent register — which is exactly the width
 * correctness the tests assert.
 */
#ifndef FAKE_CFGSPACE_H
#define FAKE_CFGSPACE_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#define FAKE_CFG_SIZE 4096

typedef struct {
    uint8_t cfg[FAKE_CFG_SIZE];
    uint8_t is_w1c[FAKE_CFG_SIZE];   /* 1 = this byte is write-1-to-clear */
    uint8_t stuck[FAKE_CFG_SIZE];    /* per-byte: bits that refuse to clear */
} fake_cfgspace;

void     fake_init(fake_cfgspace *m);                                  /* zero everything */
void     fake_poke32(fake_cfgspace *m, off_t off, uint32_t v);         /* LE store 4 bytes */
uint32_t fake_peek32(const fake_cfgspace *m, off_t off);               /* LE load 4 bytes */
/* Mark [off, off+width) as a W1C register; `stuck` (LE over the width) names bits
 * that will not clear. */
void     fake_mark_w1c(fake_cfgspace *m, off_t off, int width, uint32_t stuck);

/* The cfg_io seam implementation. ctx is a fake_cfgspace*. */
ssize_t  fake_read(void *ctx, void *buf, size_t n, off_t off);
ssize_t  fake_write(void *ctx, const void *buf, size_t n, off_t off);

#endif /* FAKE_CFGSPACE_H */
