// Host tests for the portable robot core, run against SimArm.
// Build and run: make -C firmware/test
#include <cstdio>
#include <cstring>
#include <functional>

#include "sim_hal.h"

using namespace scorbot;

static int g_fail = 0, g_pass = 0;
#define CHECK(cond, ...)                                  \
  do {                                                    \
    if (cond) {                                           \
      ++g_pass;                                           \
    } else {                                              \
      ++g_fail;                                           \
      std::printf("  FAIL %s:%d: %s  ", __FILE__, __LINE__, #cond); \
      std::printf(__VA_ARGS__);                           \
      std::printf("\n");                                  \
    }                                                     \
  } while (0)

struct Rig {
  SimArm sim;
  RobotConfig cfg = DefaultConfig();
  Robot robot{sim, cfg};
  // Run for up to `seconds`, stopping early when `done` returns true.
  bool run(float seconds, std::function<bool()> done = nullptr) {
    const float dt = 0.001f;
    for (int i = 0; i < static_cast<int>(seconds / dt); ++i) {
      robot.update(dt);
      sim.step(dt);
      if (done && done()) return true;
    }
    return !done;
  }
  bool idle() { return robot.status().mode == Mode::kIdle; }
};

static void TestEnableAndMove() {
  std::puts("move: enable and move base 30 deg");
  Rig r;
  r.run(0.05f);
  CHECK(r.robot.enable(), "%s", r.robot.lastError());
  float tgt[kJoints] = {};
  bool use[kJoints] = {};
  const float start = r.robot.status().pos[kBase];
  tgt[kBase] = start + 30.f;
  use[kBase] = true;
  CHECK(r.robot.moveTo(tgt, use, 0.5f), "%s", r.robot.lastError());
  CHECK(r.run(10.f, [&] { return r.idle(); }), "move did not finish");
  r.run(0.5f);
  float err = r.robot.status().pos[kBase] - tgt[kBase];
  CHECK(std::fabs(err) < 0.5f, "base error %.2f deg", err);
  CHECK(r.robot.status().mode == Mode::kIdle, "mode %s", ModeName(r.robot.status().mode));
}

static void TestHomeAll() {
  std::puts("home: all joints in configured order");
  Rig r;
  r.run(0.05f);
  r.robot.enable();
  CHECK(r.robot.home(-1), "%s", r.robot.lastError());
  bool ok = r.run(240.f, [&] { return r.robot.status().mode != Mode::kHoming; });
  CHECK(ok, "homing timed out in phase '%s' joint %d", r.robot.status().phase, r.robot.status().busy_joint);
  CHECK(r.robot.status().mode == Mode::kIdle, "mode %s fault '%s'", ModeName(r.robot.status().mode), r.robot.status().fault);
  for (int j = 0; j < kJoints; ++j) CHECK(r.robot.status().homed[j], "joint %d not homed", j);
  // Each switch joint should now sit at the switch edge it homed to.
  for (int j = 0; j < kSwitches; ++j) {
    float d = std::fabs(std::remainder(r.sim.jointDeg(j) - r.sim.switch_at[j], j == kRoll ? 360.f : 1e9f));
    CHECK(d < r.sim.switch_width, "joint %d ended %.2f deg from its switch", j, d);
  }
  CHECK(std::fabs(r.robot.status().pos[kGripper] - r.cfg.gripper_open_mm) < 1.0f, "gripper at %.1f mm",
        r.robot.status().pos[kGripper]);
}

static void TestHomeFromOnSwitch() {
  std::puts("home: starting with the switch already pressed");
  Rig r;
  r.sim = SimArm();
  r.sim.start_deg[kElbow] = r.sim.switch_at[kElbow];
  r.sim = SimArm();
  float jc[kJoints];
  SimArm::ToJoints(r.sim.mcount, jc);
  jc[kElbow] = r.sim.switch_at[kElbow] * r.sim.true_cpu[kElbow];
  SimArm::ToMotors(jc, r.sim.mcount);
  r.run(0.05f);
  CHECK(r.robot.status().sw[kElbow], "switch should start pressed");
  r.robot.enable();
  r.robot.home(kElbow);
  CHECK(r.run(60.f, [&] { return r.robot.status().mode != Mode::kHoming; }), "timeout");
  CHECK(r.robot.status().homed[kElbow], "elbow not homed, fault '%s'", r.robot.status().fault);
}

static void TestCalibrate() {
  std::puts("calibrate: travel span gives counts/deg");
  Rig r;
  r.run(0.05f);
  r.robot.enable();
  CHECK(r.robot.calibrate(kShoulder), "%s", r.robot.lastError());
  CHECK(r.run(240.f, [&] { return r.robot.status().mode != Mode::kCalibrating; }), "calibration timed out");
  const CalibResult& c = r.robot.calibResult(kShoulder);
  CHECK(c.result == Result::kPass, "result %s note '%s' fault '%s'", ResultName(c.result), c.note,
        r.robot.status().fault);
  float err = (c.counts_per_unit - r.sim.true_cpu[kShoulder]) / r.sim.true_cpu[kShoulder];
  CHECK(std::fabs(err) < 0.03f, "cpu %.1f vs true %.1f", c.counts_per_unit, r.sim.true_cpu[kShoulder]);
  CHECK(r.robot.applyCalibration(kShoulder), "%s", r.robot.lastError());
  // After applying, the reported angle should match the sim's true angle.
  r.run(0.5f);
  float d = r.robot.status().pos[kShoulder] - r.sim.jointDeg(kShoulder);
  CHECK(std::fabs(d) < 1.5f, "reported %.2f vs true %.2f", r.robot.status().pos[kShoulder], r.sim.jointDeg(kShoulder));
  // Roll has no stops and must be skipped.
  r.robot.calibrate(kRoll);
  CHECK(r.robot.calibResult(kRoll).result == Result::kSkipped, "roll should be skipped");
}

static void TestCalibrateGripper() {
  std::puts("calibrate: gripper close/open span");
  Rig r;
  r.run(0.05f);
  r.robot.enable();
  CHECK(r.robot.calibrate(kGripper), "%s", r.robot.lastError());
  CHECK(r.run(120.f, [&] { return r.robot.status().mode != Mode::kCalibrating; }), "timed out");
  const CalibResult& c = r.robot.calibResult(kGripper);
  CHECK(c.result == Result::kPass, "result %s '%s' fault '%s'", ResultName(c.result), c.note, r.robot.status().fault);
  CHECK(std::fabs(c.counts_per_unit - r.sim.true_cpu[kGripper]) / r.sim.true_cpu[kGripper] < 0.03f, "cpu %.1f",
        c.counts_per_unit);
}

static void TestMotorTestFindsReversedMotor() {
  std::puts("test: motor test flags a reversed motor, flip fixes it");
  Rig r;
  r.sim.motor_polarity[kElbow] = -1;
  r.run(0.05f);
  r.robot.enable();
  CHECK(r.robot.testMotor(kElbow), "%s", r.robot.lastError());
  CHECK(r.run(5.f, [&] { return r.robot.status().mode != Mode::kTest; }), "test timed out");
  CHECK(r.robot.motorTest(kElbow).result == Result::kWarn, "result %s '%s'",
        ResultName(r.robot.motorTest(kElbow).result), r.robot.motorTest(kElbow).note);
  r.robot.flipMotorDir(kElbow);
  r.robot.testMotor(kElbow);
  r.run(5.f, [&] { return r.robot.status().mode != Mode::kTest; });
  CHECK(r.robot.motorTest(kElbow).result == Result::kPass, "after flip: %s '%s'",
        ResultName(r.robot.motorTest(kElbow).result), r.robot.motorTest(kElbow).note);
}

static void TestSelfTest() {
  std::puts("test: self-test passes all motors, flags a jammed one");
  Rig r;
  r.sim.jammed[kGripper] = true;
  r.run(0.05f);
  r.robot.enable();
  CHECK(r.robot.selfTest(), "%s", r.robot.lastError());
  CHECK(r.run(20.f, [&] { return r.robot.status().mode != Mode::kTest; }), "self-test timed out");
  for (int m = 0; m < kGripper; ++m)
    CHECK(r.robot.motorTest(m).result == Result::kPass, "motor %d: %s '%s'", m, ResultName(r.robot.motorTest(m).result),
          r.robot.motorTest(m).note);
  CHECK(r.robot.motorTest(kGripper).result == Result::kFail, "jammed gripper: %s",
        ResultName(r.robot.motorTest(kGripper).result));
  CHECK(r.robot.status().enabled, "should stay enabled after the self-test");
}

static void TestEStopAndFaults() {
  std::puts("safety: E-stop latch, jog timeout, following error");
  Rig r;
  r.run(0.05f);
  r.robot.enable();
  r.sim.vm = false;
  r.run(0.05f);
  CHECK(r.robot.status().mode == Mode::kEStop, "mode %s", ModeName(r.robot.status().mode));
  CHECK(!r.robot.enable(), "enable must be refused while latched");
  r.sim.vm = true;
  CHECK(r.robot.resetFault(), "%s", r.robot.lastError());
  CHECK(r.robot.enable(), "%s", r.robot.lastError());

  r.robot.jog(kBase, 0.5f);
  r.run(0.1f);
  CHECK(r.robot.status().mode == Mode::kJog, "jogging");
  r.run(1.0f);  // no heartbeat: must stop by itself
  CHECK(r.robot.status().mode == Mode::kIdle, "jog should time out, mode %s", ModeName(r.robot.status().mode));

  r.sim.jammed[kBase] = true;
  float tgt[kJoints] = {};
  bool use[kJoints] = {};
  tgt[kBase] = r.robot.status().pos[kBase] + 40.f;
  use[kBase] = true;
  r.robot.moveTo(tgt, use, 1.f);
  r.run(5.f, [&] { return r.robot.status().mode == Mode::kFault; });
  CHECK(r.robot.status().mode == Mode::kFault, "jam should fault, mode %s", ModeName(r.robot.status().mode));
  CHECK(std::strstr(r.robot.status().fault, "following") != nullptr, "fault '%s'", r.robot.status().fault);
}

static void TestDifferentialWrist() {
  std::puts("wrist: pitch-only move drives both wrist motors the same way");
  Rig r;
  r.run(0.05f);
  r.robot.enable();
  const int32_t a0 = r.robot.status().counts[3], b0 = r.robot.status().counts[4];
  float tgt[kJoints] = {};
  bool use[kJoints] = {};
  tgt[kPitch] = r.robot.status().pos[kPitch] + 20.f;
  use[kPitch] = true;
  r.robot.moveTo(tgt, use, 1.f);
  r.run(10.f, [&] { return r.idle(); });
  const int32_t da = r.robot.status().counts[3] - a0, db = r.robot.status().counts[4] - b0;
  CHECK(da > 100 && db > 100 && std::abs(da - db) < 30, "da %d db %d", da, db);
}

int main() {
  TestEnableAndMove();
  TestHomeAll();
  TestHomeFromOnSwitch();
  TestCalibrate();
  TestCalibrateGripper();
  TestMotorTestFindsReversedMotor();
  TestSelfTest();
  TestEStopAndFaults();
  TestDifferentialWrist();
  std::printf("\n%d checks passed, %d failed\n", g_pass, g_fail);
  return g_fail ? 1 : 0;
}
