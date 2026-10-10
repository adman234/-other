// JSON command dispatcher and state snapshot, shared by REST and WebSocket.
// Portable (cJSON + core only) so it is unit-tested on the host.
#pragma once
#include "cJSON.h"
#include "core/robot.h"

struct HwInfo {
  bool expander = false, adc[2] = {false, false};
  const char* net_mode = "";  // "sta", "ap", "ap+sta"
  char ip[16] = "";
  char ssid[33] = "";
  int rssi = 0;
  uint32_t uptime_s = 0;
};

namespace api {
// Builds the full state message ({"t":"state", ...}). Caller frees.
cJSON* State(const scorbot::Robot& robot, const scorbot::RobotConfig& cfg, const HwInfo& hw);
// Runs one command message ({"cmd":"...", ...}). Fills `reply` with
// {"ok":bool,"error":"..."}; sets *config_changed when the config should be saved.
void Command(scorbot::Robot& robot, scorbot::RobotConfig& cfg, const cJSON* msg, cJSON* reply, bool* config_changed);
}  // namespace api
