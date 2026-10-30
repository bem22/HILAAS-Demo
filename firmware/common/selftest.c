#include "selftest.h"

#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

static uint32_t crc32_update(uint32_t state, const uint8_t *data, size_t length)
{
    for (size_t offset = 0; offset < length; ++offset) {
        state ^= data[offset];
        for (unsigned int bit = 0; bit < 8; ++bit) {
            uint32_t mask = UINT32_C(0) - (state & UINT32_C(1));
            state = (state >> 1) ^ (UINT32_C(0xedb88320) & mask);
        }
    }
    return state;
}

static int check(const char *name, uint32_t actual, uint32_t expected)
{
    printf("HILAAS_CHECK_V1 name=%s status=%s actual=%08" PRIx32
           " expected=%08" PRIx32 "\n",
           name, actual == expected ? "pass" : "fail", actual, expected);
    return actual == expected ? 0 : 1;
}

void hilaas_demo_tests(const char *nonce)
{
    static const uint8_t digits[] = "123456789";
    volatile uint32_t maximum = UINT32_MAX;
    uint32_t crc = ~crc32_update(UINT32_MAX, digits, sizeof(digits) - 1);
    uint32_t wrapped = maximum + UINT32_C(1);
    printf("HILAAS_TEST_V1 nonce=%s name=crc32 status=%s actual=%08" PRIx32
        " expected=cbf43926\n", nonce, crc == UINT32_C(0xcbf43926) ? "pass" : "fail", crc);
    printf("HILAAS_TEST_V1 nonce=%s name=uint32-wrap status=%s actual=%08" PRIx32
        " expected=00000000\n", nonce, wrapped == 0 ? "pass" : "fail", wrapped);
    fflush(stdout);
}

int hilaas_selftest(void)
{
    static const uint8_t digits[] = "123456789";
    static const uint8_t binary[] = {0x00, 0x01, 0x02, 0x03, 0x80, 0xfe, 0xff};
    uint32_t initial = UINT32_C(0xffffffff);
    int failures = 0;

    failures += check("crc32-empty", ~crc32_update(initial, digits, 0), UINT32_C(0));
    failures += check("crc32-standard", ~crc32_update(initial, digits, sizeof(digits) - 1),
                      UINT32_C(0xcbf43926));
    failures += check("crc32-binary", ~crc32_update(initial, binary, sizeof(binary)),
                      UINT32_C(0x784dc86b));
    uint32_t chunked = crc32_update(initial, digits, 4);
    chunked = crc32_update(chunked, digits + 4, sizeof(digits) - 1 - 4);
    failures += check("crc32-chunked", ~chunked, UINT32_C(0xcbf43926));
    failures += check("uint32-wrap", initial + UINT32_C(1), UINT32_C(0));
    printf("HILAAS_SELFTEST_V1 status=%s checks=5 failures=%d\n",
           failures == 0 ? "pass" : "fail", failures);
    fflush(stdout);
    return failures == 0 ? 0 : 1;
}