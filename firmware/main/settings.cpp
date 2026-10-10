#include "settings.h"

#include <cstring>

#ifndef SETTINGS_HOST_TEST
#include "esp_log.h"
#include "nvs.h"
#endif

using scorbot::JointConfig;
using scorbot::kJoints;
using scorbot::kMotors;
using scorbot::MotorConfig;
using scorbot::RobotConfig;

namespace {

// --- tiny field binders: one line per field, used for both directions ----
struct Io {
  cJSON* out;        // non-null: serialise into this object
  const cJSON* in;   // non-null: read from this object
  void f(const char* k, float& v) {
    if (out) cJSON_AddNumberToObject(out, k, v);
    else if (const cJSON* n = cJSON_GetObjectItemCaseSensitive(in, k); cJSON_IsNumber(n)) v = static_cast<float>(n->valuedouble);
  }
  template <typename T>
  void i(const char* k, T& v) {
    if (out) cJSON_AddNumberToObject(out, k, v);
    else if (const cJSON* n = cJSON_GetObjectItemCaseSensitive(in, k); cJSON_IsNumber(n)) v = static_cast<T>(n->valueint);
  }
  void b(const char* k, bool& v) {
    if (out) cJSON_AddBoolToObject(out, k, v);
    else if (const cJSON* n = cJSON_GetObjectItemCaseSensitive(in, k); cJSON_IsBool(n)) v = cJSON_IsTrue(n);
  }
  void s(const char* k, char* v, size_t cap) {
    if (out) cJSON_AddStringToObject(out, k, v);
    else if (const cJSON* n = cJSON_GetObjectItemCaseSensitive(in, k); cJSON_IsString(n)) {
      std::strncpy(v, n->valuestring, cap - 1);
      v[cap - 1] = 0;
    }
  }
  void str(const char* k, std::string& v) {
    if (out) cJSON_AddStringToObject(out, k, v.c_str());
    else if (const cJSON* n = cJSON_GetObjectItemCaseSensitive(in, k); cJSON_IsString(n)) v = n->valuestring;
  }
};

void BindMotor(Io io, MotorConfig& m) {
  io.i("motor_dir", m.motor_dir);
  io.i("enc_dir", m.enc_dir);
  io.f("kp", m.kp);
  io.f("ki", m.ki);
  io.f("kd", m.kd);
  io.f("i_zone", m.i_zone);
  io.f("min_duty", m.min_duty);
  io.f("max_duty", m.max_duty);
  if (m.motor_dir != -1) m.motor_dir = 1;
  if (m.enc_dir != -1) m.enc_dir = 1;
}

void BindJoint(Io io, JointConfig& j) {
  io.s("name", j.name, sizeof(j.name));
  io.b("has_switch", j.has_switch);
  io.i("home_dir", j.home_dir);
  io.f("spec_min", j.spec_min);
  io.f("spec_max", j.spec_max);
  io.b("continuous", j.continuous);
  io.f("counts_per_unit", j.counts_per_unit);
  io.b("cpu_calibrated", j.cpu_calibrated);
  io.f("home_pos", j.home_pos);
  io.f("soft_min", j.soft_min);
  io.f("soft_max", j.soft_max);
  io.f("vmax", j.vmax);
  io.f("amax", j.amax);
  io.f("home_speed", j.home_speed);
  io.f("follow_err", j.follow_err);
  io.i("stall_ms", j.stall_ms);
  if (j.home_dir != 1) j.home_dir = -1;
  if (j.counts_per_unit <= 0) j.counts_per_unit = 1;
}

void BindGlobals(Io io, RobotConfig& c) {
  io.f("roll_ratio", c.roll_ratio);
  io.f("calib_duty", c.calib_duty);
  io.f("test_duty", c.test_duty);
  io.i("test_ms", c.test_ms);
  io.f("current_limit_a", c.current_limit_a);
  io.f("gripper_open_mm", c.gripper_open_mm);
  io.i("jog_timeout_ms", c.jog_timeout_ms);
}

#ifndef SETTINGS_HOST_TEST
const char* TAG = "settings";

std::string NvsGet(const char* key) {
  nvs_handle_t h;
  if (nvs_open("scorbot", NVS_READONLY, &h) != ESP_OK) return "";
  size_t len = 0;
  std::string out;
  if (nvs_get_str(h, key, nullptr, &len) == ESP_OK && len > 0) {
    out.resize(len);
    nvs_get_str(h, key, out.data(), &len);
    out.resize(len ? len - 1 : 0);
  }
  nvs_close(h);
  return out;
}

bool NvsSet(const char* key, const char* val) {
  nvs_handle_t h;
  if (nvs_open("scorbot", NVS_READWRITE, &h) != ESP_OK) return false;
  bool ok = nvs_set_str(h, key, val) == ESP_OK && nvs_commit(h) == ESP_OK;
  nvs_close(h);
  if (!ok) ESP_LOGE(TAG, "saving %s failed", key);
  return ok;
}

bool SaveJson(const char* key, cJSON* j) {
  char* s = cJSON_PrintUnformatted(j);
  cJSON_Delete(j);
  bool ok = s && NvsSet(key, s);
  cJSON_free(s);
  return ok;
}
#endif

}  // namespace

namespace settings {

cJSON* RobotToJson(const RobotConfig& cfg_in) {
  RobotConfig cfg = cfg_in;  // binders take non-const refs
  cJSON* root = cJSON_CreateObject();
  BindGlobals(Io{root, nullptr}, cfg);
  cJSON* motors = cJSON_AddArrayToObject(root, "motors");
  for (auto& m : cfg.motor) {
    cJSON* o = cJSON_CreateObject();
    BindMotor(Io{o, nullptr}, m);
    cJSON_AddItemToArray(motors, o);
  }
  cJSON* joints = cJSON_AddArrayToObject(root, "joints");
  for (auto& j : cfg.joint) {
    cJSON* o = cJSON_CreateObject();
    BindJoint(Io{o, nullptr}, j);
    cJSON_AddItemToArray(joints, o);
  }
  cJSON* order = cJSON_AddArrayToObject(root, "home_order");
  for (int k = 0; k < kJoints; ++k) cJSON_AddItemToArray(order, cJSON_CreateNumber(cfg.home_order[k]));
  return root;
}

void RobotFromJson(const cJSON* j, RobotConfig& cfg) {
  if (!cJSON_IsObject(j)) return;
  BindGlobals(Io{nullptr, j}, cfg);
  const cJSON* motors = cJSON_GetObjectItemCaseSensitive(j, "motors");
  for (int m = 0; m < kMotors && cJSON_IsArray(motors); ++m) {
    const cJSON* o = cJSON_GetArrayItem(motors, m);
    if (cJSON_IsObject(o)) BindMotor(Io{nullptr, o}, cfg.motor[m]);
  }
  const cJSON* joints = cJSON_GetObjectItemCaseSensitive(j, "joints");
  for (int k = 0; k < kJoints && cJSON_IsArray(joints); ++k) {
    const cJSON* o = cJSON_GetArrayItem(joints, k);
    if (cJSON_IsObject(o)) BindJoint(Io{nullptr, o}, cfg.joint[k]);
  }
  const cJSON* order = cJSON_GetObjectItemCaseSensitive(j, "home_order");
  if (cJSON_IsArray(order) && cJSON_GetArraySize(order) == kJoints) {
    uint8_t tmp[kJoints];
    bool seen[kJoints] = {};
    bool ok = true;
    for (int k = 0; k < kJoints; ++k) {
      const cJSON* n = cJSON_GetArrayItem(order, k);
      int v = cJSON_IsNumber(n) ? n->valueint : -1;
      if (v < 0 || v >= kJoints || seen[v]) ok = false;
      else seen[v] = true, tmp[k] = static_cast<uint8_t>(v);
    }
    if (ok) std::memcpy(cfg.home_order, tmp, sizeof(tmp));
  }
}

cJSON* NetToJson(const NetSettings& net_in, bool include_secrets) {
  NetSettings net = net_in;
  cJSON* o = cJSON_CreateObject();
  Io io{o, nullptr};
  io.str("ssid", net.ssid);
  io.str("hostname", net.hostname);
  if (include_secrets) {
    io.str("password", net.password);
    io.str("ap_password", net.ap_password);
    io.str("api_key", net.api_key);
  } else {
    cJSON_AddBoolToObject(o, "has_password", !net.password.empty());
    cJSON_AddBoolToObject(o, "has_api_key", !net.api_key.empty());
  }
  return o;
}

void NetFromJson(const cJSON* j, NetSettings& net) {
  if (!cJSON_IsObject(j)) return;
  Io io{nullptr, j};
  io.str("ssid", net.ssid);
  io.str("password", net.password);
  io.str("ap_password", net.ap_password);
  io.str("hostname", net.hostname);
  io.str("api_key", net.api_key);
  if (net.hostname.empty()) net.hostname = "scorbot";
}

#ifndef SETTINGS_HOST_TEST
void Load(RobotConfig& cfg, NetSettings& net) {
  cfg = scorbot::DefaultConfig();
  std::string s = NvsGet("robot");
  if (!s.empty()) {
    cJSON* j = cJSON_Parse(s.c_str());
    RobotFromJson(j, cfg);
    cJSON_Delete(j);
  }
  s = NvsGet("net");
  if (!s.empty()) {
    cJSON* j = cJSON_Parse(s.c_str());
    NetFromJson(j, net);
    cJSON_Delete(j);
  }
}

bool SaveRobot(const RobotConfig& cfg) { return SaveJson("robot", RobotToJson(cfg)); }
bool SaveNet(const NetSettings& net) { return SaveJson("net", NetToJson(net, true)); }
#endif

}  // namespace settings
