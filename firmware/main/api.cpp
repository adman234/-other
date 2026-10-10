#include "api.h"

#include <cmath>
#include <cstring>

using namespace scorbot;

namespace {

double R(float v, int digits) {  // rounded for compact JSON; NAN stays NAN -> null
  if (std::isnan(v)) return NAN;
  const double p = std::pow(10.0, digits);
  return std::round(v * p) / p;
}

template <typename T, typename F>
cJSON* Arr(const T* v, int n, F conv) {
  cJSON* a = cJSON_CreateArray();
  for (int i = 0; i < n; ++i) cJSON_AddItemToArray(a, conv(v[i]));
  return a;
}

cJSON* Num(double v) { return std::isnan(v) ? cJSON_CreateNull() : cJSON_CreateNumber(v); }

int IntField(const cJSON* msg, const char* k, int dflt) {
  const cJSON* n = cJSON_GetObjectItemCaseSensitive(msg, k);
  return cJSON_IsNumber(n) ? n->valueint : dflt;
}

double NumField(const cJSON* msg, const char* k, double dflt) {
  const cJSON* n = cJSON_GetObjectItemCaseSensitive(msg, k);
  return cJSON_IsNumber(n) ? n->valuedouble : dflt;
}

}  // namespace

namespace api {

cJSON* State(const Robot& robot, const RobotConfig& cfg, const HwInfo& hw) {
  const Status& s = robot.status();
  cJSON* o = cJSON_CreateObject();
  cJSON_AddStringToObject(o, "t", "state");
  cJSON_AddStringToObject(o, "mode", ModeName(s.mode));
  cJSON_AddBoolToObject(o, "enabled", s.enabled);
  cJSON_AddStringToObject(o, "fault", s.fault);
  cJSON_AddStringToObject(o, "phase", s.phase);
  cJSON_AddNumberToObject(o, "busy", s.busy_joint);
  cJSON_AddBoolToObject(o, "vm_ok", s.vm_ok);
  cJSON_AddItemToObject(o, "vm", Num(R(s.vm, 2)));
  cJSON_AddBoolToObject(o, "drv_fault", s.driver_fault);

  cJSON_AddItemToObject(o, "pos", Arr(s.pos, kJoints, [](float v) { return Num(R(v, 2)); }));
  cJSON_AddItemToObject(o, "tgt", Arr(s.target, kJoints, [](float v) { return Num(R(v, 2)); }));
  cJSON_AddItemToObject(o, "homed", Arr(s.homed, kJoints, [](bool v) { return cJSON_CreateBool(v); }));
  cJSON_AddItemToObject(o, "counts", Arr(s.counts, kMotors, [](int32_t v) { return cJSON_CreateNumber(v); }));
  cJSON_AddItemToObject(o, "duty", Arr(s.duty, kMotors, [](float v) { return Num(R(v, 3)); }));
  cJSON_AddItemToObject(o, "cur", Arr(s.current, kMotors, [](float v) { return Num(R(v, 3)); }));
  cJSON_AddItemToObject(o, "sw", Arr(s.sw, kSwitches, [](bool v) { return cJSON_CreateBool(v); }));
  cJSON_AddItemToObject(o, "sw_on", Arr(s.sw_seen_on, kSwitches, [](bool v) { return cJSON_CreateBool(v); }));
  cJSON_AddItemToObject(o, "sw_off", Arr(s.sw_seen_off, kSwitches, [](bool v) { return cJSON_CreateBool(v); }));
  cJSON_AddItemToObject(o, "emin", Arr(s.enc_min, kMotors, [](int32_t v) { return cJSON_CreateNumber(v); }));
  cJSON_AddItemToObject(o, "emax", Arr(s.enc_max, kMotors, [](int32_t v) { return cJSON_CreateNumber(v); }));
  bool cal[kJoints];
  for (int j = 0; j < kJoints; ++j) cal[j] = cfg.joint[j].cpu_calibrated;
  cJSON_AddItemToObject(o, "calibrated", Arr(cal, kJoints, [](bool v) { return cJSON_CreateBool(v); }));

  cJSON* tests = cJSON_AddArrayToObject(o, "mtest");
  for (int m = 0; m < kMotors; ++m) {
    const MotorTestResult& r = robot.motorTest(m);
    cJSON* t = cJSON_CreateObject();
    cJSON_AddStringToObject(t, "r", ResultName(r.result));
    cJSON_AddNumberToObject(t, "fwd", r.delta_fwd);
    cJSON_AddNumberToObject(t, "rev", r.delta_rev);
    cJSON_AddItemToObject(t, "i", Num(R(r.peak_current, 2)));
    cJSON_AddStringToObject(t, "note", r.note);
    cJSON_AddItemToArray(tests, t);
  }
  cJSON* cals = cJSON_AddArrayToObject(o, "cal");
  for (int j = 0; j < kJoints; ++j) {
    const CalibResult& r = robot.calibResult(j);
    cJSON* c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "r", ResultName(r.result));
    cJSON_AddNumberToObject(c, "min", r.min_counts);
    cJSON_AddNumberToObject(c, "max", r.max_counts);
    cJSON_AddItemToObject(c, "cpu", Num(R(r.counts_per_unit, 2)));
    cJSON_AddItemToObject(c, "home", Num(R(r.home_pos, 2)));
    cJSON_AddItemToObject(c, "smin", Num(R(r.soft_min, 1)));
    cJSON_AddItemToObject(c, "smax", Num(R(r.soft_max, 1)));
    cJSON_AddStringToObject(c, "note", r.note);
    cJSON_AddItemToArray(cals, c);
  }

  cJSON* h = cJSON_AddObjectToObject(o, "hw");
  cJSON_AddBoolToObject(h, "expander", hw.expander);
  cJSON_AddBoolToObject(h, "adc0", hw.adc[0]);
  cJSON_AddBoolToObject(h, "adc1", hw.adc[1]);
  cJSON_AddStringToObject(h, "net", hw.net_mode);
  cJSON_AddStringToObject(h, "ip", hw.ip);
  cJSON_AddStringToObject(h, "ssid", hw.ssid);
  cJSON_AddNumberToObject(h, "rssi", hw.rssi);
  cJSON_AddNumberToObject(h, "uptime", hw.uptime_s);
  return o;
}

void Command(Robot& robot, RobotConfig& cfg, const cJSON* msg, cJSON* reply, bool* config_changed) {
  const cJSON* c = cJSON_GetObjectItemCaseSensitive(msg, "cmd");
  const char* cmd = cJSON_IsString(c) ? c->valuestring : "";
  bool ok = true;
  const char* err = nullptr;
  const int joint = IntField(msg, "joint", -1);
  const int motor = IntField(msg, "motor", -1);
  *config_changed = false;

  if (!std::strcmp(cmd, "enable")) {
    ok = robot.enable();
  } else if (!std::strcmp(cmd, "disable")) {
    robot.disable();
  } else if (!std::strcmp(cmd, "stop")) {
    robot.stop();
  } else if (!std::strcmp(cmd, "reset")) {
    ok = robot.resetFault();
  } else if (!std::strcmp(cmd, "jog")) {
    ok = robot.jog(joint, static_cast<float>(NumField(msg, "speed", 0)));
  } else if (!std::strcmp(cmd, "move")) {
    const cJSON* pos = cJSON_GetObjectItemCaseSensitive(msg, "pos");
    float tgt[kJoints] = {};
    bool use[kJoints] = {};
    for (int j = 0; j < kJoints && cJSON_IsArray(pos); ++j) {
      const cJSON* v = cJSON_GetArrayItem(pos, j);
      if (cJSON_IsNumber(v)) {
        tgt[j] = static_cast<float>(v->valuedouble);
        use[j] = true;
      }
    }
    ok = robot.moveTo(tgt, use, static_cast<float>(NumField(msg, "speed", 0.5)));
  } else if (!std::strcmp(cmd, "home")) {
    ok = robot.home(joint);
  } else if (!std::strcmp(cmd, "calibrate")) {
    ok = robot.calibrate(joint);
  } else if (!std::strcmp(cmd, "apply_calibration")) {
    if (joint < 0) {
      bool any = false;
      for (int j = 0; j < kJoints; ++j) any |= robot.applyCalibration(j);
      ok = any;
    } else {
      ok = robot.applyCalibration(joint);
    }
    *config_changed = ok;
  } else if (!std::strcmp(cmd, "test_motor")) {
    ok = robot.testMotor(motor);
  } else if (!std::strcmp(cmd, "self_test")) {
    ok = robot.selfTest();
  } else if (!std::strcmp(cmd, "flip_motor")) {
    robot.flipMotorDir(motor);
    *config_changed = true;
  } else if (!std::strcmp(cmd, "invert_joint")) {
    robot.invertJoint(joint);
    *config_changed = true;
  } else if (!std::strcmp(cmd, "zero_here")) {
    robot.setZeroHere(joint);
  } else if (!std::strcmp(cmd, "set_cpu")) {
    const double v = NumField(msg, "value", 0);
    if (v > 0) {
      robot.setCountsPerUnit(joint, static_cast<float>(v));
      *config_changed = true;
    } else {
      ok = false;
      err = "value must be > 0";
    }
  } else if (!std::strcmp(cmd, "reset_switch_test")) {
    robot.resetSwitchTest();
  } else if (!std::strcmp(cmd, "reset_encoder_test")) {
    robot.resetEncoderTest();
  } else if (!std::strcmp(cmd, "defaults")) {
    robot.disable();
    cfg = DefaultConfig();
    *config_changed = true;
  } else {
    ok = false;
    err = "unknown command";
  }
  if (!ok && !err) err = robot.lastError();
  cJSON_AddBoolToObject(reply, "ok", ok);
  if (!ok) cJSON_AddStringToObject(reply, "error", err ? err : "failed");
}

}  // namespace api
