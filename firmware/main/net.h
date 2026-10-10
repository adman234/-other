// WiFi: join the saved network; if that fails for 60 s (or none is saved),
// open an access point "Scorbot-XXXX" with a captive-portal DNS, and keep
// retrying the saved network in the background. mDNS: http://<hostname>.local
#pragma once
#include "api.h"
#include "settings.h"

namespace net {
void Start(const NetSettings& cfg);
void Fill(HwInfo& hw);  // current mode / IP / SSID / RSSI for the UI
}  // namespace net
