#include "../../common/selftest.h"
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include "esp_err.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "build_identity.h"

void app_main(void)
{
    if (hilaas_selftest() != 0) {
        return;
    }
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    uint32_t sequence = 0;
    while (true) {
        printf("HILAAS_C6_V1 mac=%02x:%02x:%02x:%02x:%02x:%02x nonce=%s seq=%" PRIu32 "\n",
               mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], HILAAS_NONCE, sequence++);
        fflush(stdout);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
