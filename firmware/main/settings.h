// Persistent settings: robot config (calibration, PID, limits) and network,
// stored as JSON in NVS. The same JSON shape is used by GET/POST /api/config.
#pragma once
#include <string>

#include "cJSON.h"
#include "core/config.h"

struct NetSettings {
  std::string ssid, password;          // home WiFi; empty = AP only
  std::string ap_password = "scorbot1";  // >= 8 chars, else the AP is open
  std::string hostname = "scorbot";
  std::string api_key;                 // empty = no key required
};

namespace settings {
void Load(scorbot::RobotConfig& cfg, NetSettings& net);
bool SaveRobot(const scorbot::RobotConfig& cfg);
bool SaveNet(const NetSettings& net);

cJSON* RobotToJson(const scorbot::RobotConfig& cfg);
// Applies only the fields present in `j`, so the UI can send partial updates.
void RobotFromJson(const cJSON* j, scorbot::RobotConfig& cfg);
cJSON* NetToJson(const NetSettings& net, bool include_secrets);
void NetFromJson(const cJSON* j, NetSettings& net);
}  // namespace settings
