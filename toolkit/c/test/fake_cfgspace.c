#include "fake_cfgspace.h"

#include <string.h>

void fake_init(fake_cfgspace *m) {
    memset(m, 0, sizeof *m);
}

void fake_poke32(fake_cfgspace *m, off_t off, uint32_t v) {
    for (int i = 0; i < 4; i++)
        m->cfg[off + i] = (uint8_t)((v >> (8 * i)) & 0xff);
}

uint32_t fake_peek32(const fake_cfgspace *m, off_t off) {
    uint32_t v = 0;
    for (int i = 0; i < 4; i++)
        v |= (uint32_t)m->cfg[off + i] << (8 * i);
    return v;
}

void fake_mark_w1c(fake_cfgspace *m, off_t off, int width, uint32_t stuck) {
    for (int i = 0; i < width; i++) {
        m->is_w1c[off + i] = 1;
        m->stuck[off + i] = (uint8_t)((stuck >> (8 * i)) & 0xff);
    }
}

ssize_t fake_read(void *ctx, void *buf, size_t n, off_t off) {
    fake_cfgspace *m = ctx;
    if (off < 0 || (size_t)off + n > FAKE_CFG_SIZE) return -1;
    memcpy(buf, m->cfg + off, n);
    return (ssize_t)n;
}

ssize_t fake_write(void *ctx, const void *buf, size_t n, off_t off) {
    fake_cfgspace *m = ctx;
    if (off < 0 || (size_t)off + n > FAKE_CFG_SIZE) return -1;
    const uint8_t *in = buf;
    for (size_t i = 0; i < n; i++) {
        off_t pos = off + (off_t)i;
        if (m->is_w1c[pos]) {
            uint8_t clr = in[i] & ~m->stuck[pos];   /* stuck bits never clear */
            m->cfg[pos] &= ~clr;
        } else {
            m->cfg[pos] = in[i];                    /* normal register: plain store */
        }
    }
    return (ssize_t)n;
}
