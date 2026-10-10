// Scorbot ER-4u controller firmware (ESP-IDF 5.3+, ESP32-WROOM-32D/32E).
//
// Tasks:
//   control  core 1, 1 kHz (esp_timer tick): Robot::update
//   io       core 0, 500 Hz: TCA9555 / ADS1115 over I2C, status LED
//   network  core 0: WiFi supervisor, captive DNS, HTTP + WebSocket (20 Hz state)
#include "core/robot.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "hal_esp32.h"
#include "net.h"
#include "nvs_flash.h"
#include "settings.h"
#include "web.h"

static const char* TAG = "main";

namespace {
scorbot::RobotConfig g_cfg;
NetSettings g_net;
Esp32Hal g_hal;
scorbot::Robot* g_robot = nullptr;
SemaphoreHandle_t g_lock = nullptr;
TaskHandle_t g_control_task = nullptr;
App g_app;

void Tick(void*) { xTaskNotifyGive(g_control_task); }

void ControlTask(void*) {
  int64_t last = esp_timer_get_time();
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    const int64_t now = esp_timer_get_time();
    float dt = (now - last) * 1e-6f;
    last = now;
    if (dt < 0.0005f) dt = 0.0005f;
    if (dt > 0.005f) dt = 0.005f;
    xSemaphoreTake(g_lock, portMAX_DELAY);
    g_robot->update(dt);
    const scorbot::Mode mode = g_robot->status().mode;
    xSemaphoreGive(g_lock);
    using scorbot::Mode;
    g_hal.setLedPattern(mode == Mode::kFault || mode == Mode::kEStop ? 3
                        : mode == Mode::kDisabled                   ? 2
                                                                    : 1);
  }
}

void IoTask(void*) { g_hal.ioLoop(); }
}  // namespace

extern "C" void app_main() {
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    ESP_ERROR_CHECK(nvs_flash_init());
  }
  settings::Load(g_cfg, g_net);
  ESP_ERROR_CHECK(g_hal.begin());
  static scorbot::Robot robot(g_hal, g_cfg);
  g_robot = &robot;
  g_lock = xSemaphoreCreateMutex();

  xTaskCreatePinnedToCore(IoTask, "io", 4096, nullptr, 10, nullptr, 0);
  xTaskCreatePinnedToCore(ControlTask, "control", 6144, nullptr, configMAX_PRIORITIES - 2, &g_control_task, 1);
  esp_timer_create_args_t targs = {};
  targs.callback = Tick;
  targs.name = "ctl_tick";
  esp_timer_handle_t timer;
  ESP_ERROR_CHECK(esp_timer_create(&targs, &timer));
  ESP_ERROR_CHECK(esp_timer_start_periodic(timer, 1000));

  net::Start(g_net);
  g_app = App{g_robot, &g_cfg, &g_net, &g_hal, g_lock};
  web::Start(&g_app);
  ESP_LOGI(TAG, "ready: http://%s.local or the IP shown on the serial log", g_net.hostname.c_str());
}
