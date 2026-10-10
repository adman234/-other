#include "net.h"

#include <cstring>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "mdns.h"

static const char* TAG = "net";

namespace {
NetSettings g_cfg;
esp_netif_t* g_sta = nullptr;
esp_netif_t* g_ap = nullptr;
volatile bool g_connected = false;
volatile bool g_ap_on = false;
char g_ap_ssid[33] = "";
int64_t g_connected_since_us = 0;

void StartAp() {
  if (g_ap_on) return;
  wifi_config_t ap = {};
  std::strncpy(reinterpret_cast<char*>(ap.ap.ssid), g_ap_ssid, sizeof(ap.ap.ssid));
  ap.ap.ssid_len = std::strlen(g_ap_ssid);
  ap.ap.channel = 1;
  ap.ap.max_connection = 4;
  if (g_cfg.ap_password.size() >= 8) {
    std::strncpy(reinterpret_cast<char*>(ap.ap.password), g_cfg.ap_password.c_str(), sizeof(ap.ap.password) - 1);
    ap.ap.authmode = WIFI_AUTH_WPA2_PSK;
  } else {
    ap.ap.authmode = WIFI_AUTH_OPEN;
  }
  esp_wifi_set_mode(g_cfg.ssid.empty() ? WIFI_MODE_AP : WIFI_MODE_APSTA);
  esp_wifi_set_config(WIFI_IF_AP, &ap);
  g_ap_on = true;
  ESP_LOGW(TAG, "access point %s up at 192.168.4.1", g_ap_ssid);
}

void StopAp() {
  if (!g_ap_on) return;
  esp_wifi_set_mode(WIFI_MODE_STA);
  g_ap_on = false;
  ESP_LOGI(TAG, "access point closed (joined %s)", g_cfg.ssid.c_str());
}

void OnEvent(void*, esp_event_base_t base, int32_t id, void*) {
  if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
    if (!g_cfg.ssid.empty()) esp_wifi_connect();
  } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
    g_connected = false;
  } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
    g_connected = true;
    g_connected_since_us = esp_timer_get_time();
    ESP_LOGI(TAG, "joined %s", g_cfg.ssid.c_str());
  }
}

// Supervises the STA/AP fallback described in net.h.
void Supervisor(void*) {
  int64_t down_since = esp_timer_get_time();
  int64_t last_retry = 0;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    const int64_t now = esp_timer_get_time();
    if (g_cfg.ssid.empty()) {
      StartAp();
      continue;
    }
    if (g_connected) {
      down_since = now;
      if (g_ap_on && now - g_connected_since_us > 120 * 1000000LL) {
        wifi_sta_list_t list = {};
        if (esp_wifi_ap_get_sta_list(&list) == ESP_OK && list.num == 0) StopAp();
      }
      continue;
    }
    if (now - down_since > 60 * 1000000LL) StartAp();
    if (now - last_retry > 30 * 1000000LL) {
      last_retry = now;
      esp_wifi_connect();
    }
  }
}

// Captive portal: answer every DNS A query with 192.168.4.1.
void DnsTask(void*) {
  int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  sockaddr_in addr = {};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(53);
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  if (sock < 0 || bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
    ESP_LOGE(TAG, "DNS socket failed");
    vTaskDelete(nullptr);
    return;
  }
  uint8_t buf[512];
  for (;;) {
    sockaddr_in from = {};
    socklen_t fl = sizeof(from);
    int n = recvfrom(sock, buf, sizeof(buf) - 16, 0, reinterpret_cast<sockaddr*>(&from), &fl);
    if (n < 12 || !g_ap_on) continue;
    // Find the end of the first question (name + type + class).
    int p = 12;
    while (p < n && buf[p] != 0) p += buf[p] + 1;
    p += 5;
    if (p > n) continue;
    buf[2] = 0x81;  // response, recursion desired
    buf[3] = 0x80;  // recursion available, no error
    buf[6] = 0;     // one answer
    buf[7] = 1;
    buf[8] = buf[9] = buf[10] = buf[11] = 0;
    const uint8_t ans[] = {0xC0, 0x0C, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4, 192, 168, 4, 1};
    std::memcpy(buf + p, ans, sizeof(ans));
    sendto(sock, buf, p + sizeof(ans), 0, reinterpret_cast<sockaddr*>(&from), fl);
  }
}

}  // namespace

namespace net {

void Start(const NetSettings& cfg) {
  g_cfg = cfg;
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
  std::snprintf(g_ap_ssid, sizeof(g_ap_ssid), "Scorbot-%02X%02X", mac[4], mac[5]);

  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());
  g_sta = esp_netif_create_default_wifi_sta();
  g_ap = esp_netif_create_default_wifi_ap();
  esp_netif_set_hostname(g_sta, g_cfg.hostname.c_str());
  wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&init));
  esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, OnEvent, nullptr);
  esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, OnEvent, nullptr);
  esp_wifi_set_storage(WIFI_STORAGE_RAM);

  if (!g_cfg.ssid.empty()) {
    wifi_config_t sta = {};
    std::strncpy(reinterpret_cast<char*>(sta.sta.ssid), g_cfg.ssid.c_str(), sizeof(sta.sta.ssid) - 1);
    std::strncpy(reinterpret_cast<char*>(sta.sta.password), g_cfg.password.c_str(), sizeof(sta.sta.password) - 1);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &sta);
  } else {
    esp_wifi_set_mode(WIFI_MODE_AP);
  }
  ESP_ERROR_CHECK(esp_wifi_start());
  esp_wifi_set_ps(WIFI_PS_NONE);  // lowest latency for jogging

  if (mdns_init() == ESP_OK) {
    mdns_hostname_set(g_cfg.hostname.c_str());
    mdns_instance_name_set("Scorbot ER-4u controller");
    mdns_service_add(nullptr, "_http", "_tcp", 80, nullptr, 0);
  }
  xTaskCreatePinnedToCore(Supervisor, "wifi_sup", 3072, nullptr, 3, nullptr, 0);
  xTaskCreatePinnedToCore(DnsTask, "dns", 3072, nullptr, 3, nullptr, 0);
}

void Fill(HwInfo& hw) {
  hw.net_mode = g_ap_on ? (g_connected ? "ap+sta" : "ap") : (g_connected ? "sta" : "connecting");
  esp_netif_ip_info_t ip = {};
  if (g_connected && esp_netif_get_ip_info(g_sta, &ip) == ESP_OK)
    std::snprintf(hw.ip, sizeof(hw.ip), IPSTR, IP2STR(&ip.ip));
  else if (g_ap_on)
    std::snprintf(hw.ip, sizeof(hw.ip), "192.168.4.1");
  else
    hw.ip[0] = 0;
  std::snprintf(hw.ssid, sizeof(hw.ssid), "%s", g_connected ? g_cfg.ssid.c_str() : g_ap_ssid);
  wifi_ap_record_t rec = {};
  hw.rssi = (g_connected && esp_wifi_sta_get_ap_info(&rec) == ESP_OK) ? rec.rssi : 0;
}

}  // namespace net
