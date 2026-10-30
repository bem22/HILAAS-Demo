#include <zephyr/sys/printk.h>
#include "selftest.h"

int main(void)
{
    printk("HILAAS_BUILD_V1 commit=%s run_id=%s nonce=%s board=%s\n",
           HILAAS_COMMIT, HILAAS_RUN_ID, HILAAS_NONCE, HILAAS_BOARD);
    return hilaas_selftest();
}