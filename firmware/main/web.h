// HTTP + WebSocket server: serves the embedded web UI, the REST API
// (/api/state, /api/cmd, /api/config, /api/wifi, /api/reboot) and /ws,
// which streams state at 20 Hz and accepts the same commands as /api/cmd.
#pragma once
#include "core/robot.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "hal_esp32.h"
#include "settings.h"

struct App {
  scorbot::Robot* robot;
  scorbot::RobotConfig* cfg;
  NetSettings* net;
  Esp32Hal* hal;
  SemaphoreHandle_t lock;  // guards robot + cfg (the control loop holds it each tick)
};

namespace web {
void Start(App* app);
}
