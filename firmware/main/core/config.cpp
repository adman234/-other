#include "config.h"

#include <cstring>

namespace scorbot {

namespace {
void SetJoint(JointConfig& j, const char* name, bool sw, int8_t home_dir, float lo, float hi, float cpu,
              float vmax, float home_speed) {
  std::strncpy(j.name, name, sizeof(j.name) - 1);
  j.has_switch = sw;
  j.home_dir = home_dir;
  j.spec_min = lo;
  j.spec_max = hi;
  j.counts_per_unit = cpu;
  j.soft_min = lo + 2.f;
  j.soft_max = hi - 2.f;
  j.vmax = vmax;
  j.home_speed = home_speed;
}
}  // namespace

RobotConfig DefaultConfig() {
  RobotConfig c;
  // Ranges: SCORBOT-ER 4u User Manual, ch. 2. counts_per_unit values are
  // estimates (20-slot encoder x4 = 80 counts/motor rev, 127.1:1 or 65.5:1
  // gearhead, unknown final stage) until the travel calibration measures them.
  SetJoint(c.joint[kBase], "base", true, -1, -155.f, 155.f, 140.f, 40.f, 8.f);
  SetJoint(c.joint[kShoulder], "shoulder", true, -1, -35.f, 130.f, 140.f, 30.f, 6.f);
  SetJoint(c.joint[kElbow], "elbow", true, -1, -130.f, 130.f, 140.f, 30.f, 6.f);
  SetJoint(c.joint[kPitch], "pitch", true, -1, -130.f, 130.f, 75.f, 60.f, 10.f);
  SetJoint(c.joint[kRoll], "roll", true, -1, -570.f, 570.f, 75.f, 90.f, 15.f);
  SetJoint(c.joint[kGripper], "gripper", false, -1, 0.f, 75.f, 100.f, 20.f, 8.f);
  c.joint[kRoll].continuous = true;
  c.joint[kShoulder].stall_ms = 400;  // reference ER III build needed longer windows
  c.joint[kPitch].stall_ms = 400;
  c.joint[kGripper].follow_err = 6.f;
  return c;
}

}  // namespace scorbot
