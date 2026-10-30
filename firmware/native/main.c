#include "../common/selftest.h"
#include <stdio.h>

#ifndef HILAAS_BUILD_COMMIT
#define HILAAS_BUILD_COMMIT "local"
#endif
#ifndef HILAAS_BUILD_RUN_ID
#define HILAAS_BUILD_RUN_ID "local"
#endif
#ifndef HILAAS_BUILD_NONCE
#define HILAAS_BUILD_NONCE "local"
#endif

int main(void)
{
    printf("HILAAS_BUILD_V1 commit=%s run_id=%s nonce=%s\n",
           HILAAS_BUILD_COMMIT, HILAAS_BUILD_RUN_ID, HILAAS_BUILD_NONCE);
    return hilaas_selftest();
}