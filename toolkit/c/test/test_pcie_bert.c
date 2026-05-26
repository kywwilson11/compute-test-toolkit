/*
 * Unit tests for pcie_bert_core — the W1C primitive, capability walks, the
 * per-iteration accounting, and the pure parsers. Runs off-hardware against the
 * in-memory fake_cfgspace model (true W1C + stuck bits). This is the C mirror of
 * what MockBackend already proves on the Python side.
 */
#include "unity.h"
#include "pcie_bert_core.h"
#include "fake_cfgspace.h"

#include <math.h>

static fake_cfgspace M;
static cfg_io IO;

void setUp(void) {
    fake_init(&M);
    IO = (cfg_io){ fake_read, fake_write, &M };
}
void tearDown(void) {}

/* --- cfg_read / cfg_write: little-endian assembly at 1/2/4 bytes -------------- */
void test_cfg_read_write_little_endian(void) {
    fake_poke32(&M, 0x10, 0x11223344u);
    uint32_t v = 0;
    TEST_ASSERT_EQUAL_INT(0, cfg_read(&IO, 0x10, 4, &v));
    TEST_ASSERT_EQUAL_HEX32(0x11223344u, v);
    TEST_ASSERT_EQUAL_INT(0, cfg_read(&IO, 0x10, 1, &v));
    TEST_ASSERT_EQUAL_HEX32(0x44u, v);
    TEST_ASSERT_EQUAL_INT(0, cfg_read(&IO, 0x11, 1, &v));
    TEST_ASSERT_EQUAL_HEX32(0x33u, v);
    TEST_ASSERT_EQUAL_INT(0, cfg_read(&IO, 0x10, 2, &v));
    TEST_ASSERT_EQUAL_HEX32(0x3344u, v);

    /* write to a normal (non-W1C) register stores plainly */
    TEST_ASSERT_EQUAL_INT(0, cfg_write(&IO, 0x20, 4, 0xAABBCCDDu));
    TEST_ASSERT_EQUAL_HEX32(0xAABBCCDDu, fake_peek32(&M, 0x20));
    TEST_ASSERT_EQUAL_INT(0, cfg_write(&IO, 0x20, 1, 0xEEu));
    TEST_ASSERT_EQUAL_HEX32(0xAABBCCEEu, fake_peek32(&M, 0x20));
}

/* --- read_and_clear: the W1C primitive ---------------------------------------- */
void test_read_and_clear_clears_set_bits(void) {
    fake_mark_w1c(&M, 0xB0, 4, 0);
    fake_poke32(&M, 0xB0, 0x000000C0u);   /* bits 6,7 latched */
    TEST_ASSERT_EQUAL_HEX32(0xC0u, read_and_clear(&IO, 0xB0, 0xFFFFFFFFu, 4));
    TEST_ASSERT_EQUAL_HEX32(0u, fake_peek32(&M, 0xB0));        /* cleared */
    TEST_ASSERT_EQUAL_HEX32(0u, read_and_clear(&IO, 0xB0, 0xFFFFFFFFu, 4)); /* nothing left */
}

void test_read_and_clear_honors_mask(void) {
    fake_mark_w1c(&M, 0xA0, 4, 0);
    fake_poke32(&M, 0xA0, 0x0000000Fu);
    /* only bits 0 and 2 are in the mask: only those are returned and cleared */
    TEST_ASSERT_EQUAL_HEX32(0x5u, read_and_clear(&IO, 0xA0, 0x00000005u, 4));
    TEST_ASSERT_EQUAL_HEX32(0xAu, fake_peek32(&M, 0xA0));      /* bits 1,3 remain */
}

void test_read_and_clear_stuck_bit_persists(void) {
    fake_mark_w1c(&M, 0x90, 4, 0x00000001u);   /* bit 0 is stuck */
    fake_poke32(&M, 0x90, 0x00000001u);
    TEST_ASSERT_EQUAL_HEX32(0x1u, read_and_clear(&IO, 0x90, 0xFFFFFFFFu, 4));
    TEST_ASSERT_EQUAL_HEX32(0x1u, fake_peek32(&M, 0x90) & 0x1u);  /* did NOT clear */
    TEST_ASSERT_EQUAL_HEX32(0x1u, read_and_clear(&IO, 0x90, 0xFFFFFFFFu, 4)); /* still set */
}

/* The crown jewel: a 2-byte W1C must not touch the adjacent register. Device Status
 * is 16-bit at PCIe-cap+0x0A; Link Capabilities sits at +0x0C. A 4-byte access would
 * span both — so width correctness is a real bug class. */
void test_read_and_clear_width_no_adjacent_clobber(void) {
    fake_mark_w1c(&M, 0x80, 2, 0);        /* a 2-byte Device-Status-like W1C reg */
    fake_poke32(&M, 0x80, 0x00000001u);   /* DEVSTA_CORR set */
    fake_poke32(&M, 0x82, 0x0000ABCDu);   /* sentinel in the adjacent register */

    uint32_t got = read_and_clear(&IO, 0x80, DEVSTA_CORR | DEVSTA_NONFATAL | DEVSTA_FATAL, 2);
    TEST_ASSERT_EQUAL_HEX32(0x1u, got);
    TEST_ASSERT_EQUAL_HEX32(0u, fake_peek32(&M, 0x80) & 0xFFFFu);   /* status cleared */
    TEST_ASSERT_EQUAL_HEX32(0xABCDu, fake_peek32(&M, 0x82) & 0xFFFFu); /* adjacent intact */
}

/* --- find_ext_cap: extended capability list from 0x100 ------------------------ */
void test_find_ext_cap_finds_chained_cap(void) {
    /* 0x100: some other cap (id 0x000B), next -> 0x140 */
    fake_poke32(&M, 0x100, ((uint32_t)0x140 << 20) | 0x000Bu);
    /* 0x140: AER (id 0x0001), next -> 0 */
    fake_poke32(&M, 0x140, 0x00000001u);
    TEST_ASSERT_EQUAL_INT(0x140, (int)find_ext_cap(&IO, ECAP_AER));
}

void test_find_ext_cap_first_entry(void) {
    fake_poke32(&M, 0x100, 0x00000001u);  /* AER right at 0x100 */
    TEST_ASSERT_EQUAL_INT(0x100, (int)find_ext_cap(&IO, ECAP_AER));
}

void test_find_ext_cap_absent_returns_zero(void) {
    fake_poke32(&M, 0x100, 0x00000000u);
    TEST_ASSERT_EQUAL_INT(0, (int)find_ext_cap(&IO, ECAP_AER));
    fake_poke32(&M, 0x100, 0xFFFFFFFFu);  /* unimplemented config space reads as all-ones */
    TEST_ASSERT_EQUAL_INT(0, (int)find_ext_cap(&IO, ECAP_AER));
}

void test_find_ext_cap_self_loop_terminates(void) {
    /* a malformed chain whose next-pointer points at itself must terminate (guard),
     * not hang, and must not match the wanted id */
    fake_poke32(&M, 0x100, ((uint32_t)0x100 << 20) | 0x0002u);
    TEST_ASSERT_EQUAL_INT(0, (int)find_ext_cap(&IO, ECAP_AER));
}

/* --- find_cap: legacy capability list from the 0x34 pointer -------------------- */
void test_find_cap_finds_pcie_cap(void) {
    fake_poke32(&M, 0x34, 0x00000040u);   /* cap pointer -> 0x40 */
    fake_poke32(&M, 0x40, 0x00000010u);   /* id 0x10 (PCIe), next byte 0x41 = 0 */
    TEST_ASSERT_EQUAL_INT(0x40, (int)find_cap(&IO, CAP_PCIE));
}

void test_find_cap_absent_returns_zero(void) {
    fake_poke32(&M, 0x34, 0x00000040u);
    fake_poke32(&M, 0x40, 0x00000005u);   /* some other cap, next = 0 */
    TEST_ASSERT_EQUAL_INT(0, (int)find_cap(&IO, CAP_PCIE));
}

/* --- accounting: per-correctable counting and uncorrectable dedup ------------- */
void test_account_correctable_counts_named_bits(void) {
    long events[16] = {0};
    long total = 0;
    /* BadTLP (bit 6, index 1) and BadDLLP (bit 7, index 2) */
    account_correctable((1u << 6) | (1u << 7), AER_COR_BITS, N_AER_COR_BITS, events, &total);
    TEST_ASSERT_EQUAL_INT(2, (int)total);
    TEST_ASSERT_EQUAL_INT(0, (int)events[0]);  /* RxErr (bit 0) not set */
    TEST_ASSERT_EQUAL_INT(1, (int)events[1]);  /* BadTLP */
    TEST_ASSERT_EQUAL_INT(1, (int)events[2]);  /* BadDLLP */
}

void test_account_uncorrectable_counts_each_type_once(void) {
    uint32_t seen = 0;
    long total = 0;
    account_uncorrectable(0x5u, &seen, &total);     /* bits 0,2 -> +2 */
    TEST_ASSERT_EQUAL_INT(2, (int)total);
    account_uncorrectable(0x5u, &seen, &total);     /* same bits -> +0 (latch persists) */
    TEST_ASSERT_EQUAL_INT(2, (int)total);
    account_uncorrectable(0xDu, &seen, &total);     /* adds bit 3 -> +1 */
    TEST_ASSERT_EQUAL_INT(3, (int)total);
    TEST_ASSERT_EQUAL_HEX32(0xDu, seen);
}

/* --- pure helpers ------------------------------------------------------------- */
void test_link_bits_per_sec(void) {
    /* Gen4 x16: 16 GT/s * 16 lanes * 128/130 */
    double g4 = link_bits_per_sec(4, 16);
    TEST_ASSERT_TRUE(fabs(g4 - 16e9 * 16 * (128.0 / 130.0)) < 1e6);
    /* Gen1 x1: 2.5 GT/s * 1 * 8/10 = 2e9 */
    TEST_ASSERT_TRUE(fabs(link_bits_per_sec(1, 1) - 2.0e9) < 1.0);
    /* out of range -> 0 */
    TEST_ASSERT_TRUE(link_bits_per_sec(0, 16) == 0.0);
    TEST_ASSERT_TRUE(link_bits_per_sec(7, 16) == 0.0);
}

void test_speed_code_from_str(void) {
    TEST_ASSERT_EQUAL_INT(1, speed_code_from_str("2.5 GT/s"));
    TEST_ASSERT_EQUAL_INT(2, speed_code_from_str("5.0 GT/s"));
    TEST_ASSERT_EQUAL_INT(3, speed_code_from_str("8.0 GT/s"));
    TEST_ASSERT_EQUAL_INT(4, speed_code_from_str("16.0 GT/s"));
    TEST_ASSERT_EQUAL_INT(5, speed_code_from_str("32.0 GT/s"));
    TEST_ASSERT_EQUAL_INT(6, speed_code_from_str("64.0 GT/s"));
    TEST_ASSERT_EQUAL_INT(0, speed_code_from_str("garbage"));
    TEST_ASSERT_EQUAL_INT(0, speed_code_from_str(""));
}

void test_valid_bdf(void) {
    TEST_ASSERT_TRUE(valid_bdf("0000:03:00.0"));
    TEST_ASSERT_TRUE(valid_bdf("abcd:ff:1f.7"));
    TEST_ASSERT_FALSE(valid_bdf("0000:03:00.8"));   /* function > 7 */
    TEST_ASSERT_FALSE(valid_bdf("0000:03:00.0x"));  /* trailing junk */
    TEST_ASSERT_FALSE(valid_bdf("../../etc/passwd"));
    TEST_ASSERT_FALSE(valid_bdf("0000:03:00"));     /* missing .F */
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_cfg_read_write_little_endian);
    RUN_TEST(test_read_and_clear_clears_set_bits);
    RUN_TEST(test_read_and_clear_honors_mask);
    RUN_TEST(test_read_and_clear_stuck_bit_persists);
    RUN_TEST(test_read_and_clear_width_no_adjacent_clobber);
    RUN_TEST(test_find_ext_cap_finds_chained_cap);
    RUN_TEST(test_find_ext_cap_first_entry);
    RUN_TEST(test_find_ext_cap_absent_returns_zero);
    RUN_TEST(test_find_ext_cap_self_loop_terminates);
    RUN_TEST(test_find_cap_finds_pcie_cap);
    RUN_TEST(test_find_cap_absent_returns_zero);
    RUN_TEST(test_account_correctable_counts_named_bits);
    RUN_TEST(test_account_uncorrectable_counts_each_type_once);
    RUN_TEST(test_link_bits_per_sec);
    RUN_TEST(test_speed_code_from_str);
    RUN_TEST(test_valid_bdf);
    return UNITY_END();
}
