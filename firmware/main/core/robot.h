// Portable robot core: joint/motor mapping, PID, trajectories, homing,
// travel calibration and hardware self-tests. Hardware is reached only
// through the Hal interface, so this compiles and runs on a PC against the
// simulator in firmware/test.
#pragma once
#include <cmath>
#include <cstdint>

#include "config.h"

namespace scorbot {

class Hal {
 public:
  virtual ~Hal() = default;
  virtual int32_t encoder(int motor) = 0;          // raw accumulated counts
  virtual void setMotor(int motor, float duty) = 0;  // -1..1, sign = direction
  virtual void enableDrivers(bool on) = 0;         // nSLEEP
  virtual bool homeSwitch(int sw) = 0;             // true = pressed
  virtual bool driverFault() = 0;                  // any nFAULT low
  virtual bool motorPowerOk() = 0;                 // VM present (E-stop closed)
  virtual float current(int motor) = 0;            // amps, NAN if no ADC
  virtual float motorVolts() = 0;                  // VM, NAN if no ADC
  virtual uint32_t millis() = 0;
};

enum class Mode : uint8_t { kDisabled, kIdle, kMoving, kJog, kHoming, kCalibrating, kTest, kFault, kEStop };
const char* ModeName(Mode m);

enum class TestKind : uint8_t { kNone, kMotor, kSelfTest };
enum class Result : uint8_t { kPending, kPass, kWarn, kFail, kSkipped };
const char* ResultName(Result r);

struct MotorTestResult {
  Result result = Result::kPending;
  int32_t delta_fwd = 0, delta_rev = 0;  // encoder change for +duty / -duty
  float peak_current = NAN;
  char note[64] = "";
};

struct CalibResult {
  Result result = Result::kPending;
  int32_t min_counts = 0, max_counts = 0, home_counts = 0;  // joint counts
  float counts_per_unit = 0, home_pos = 0, soft_min = 0, soft_max = 0;
  char note[64] = "";
};

struct Status {
  Mode mode = Mode::kDisabled;
  bool enabled = false;
  bool homed[kJoints] = {};
  float pos[kJoints] = {};      // joint position, degrees (gripper mm)
  float target[kJoints] = {};
  int32_t counts[kMotors] = {};
  float duty[kMotors] = {};
  float current[kMotors] = {};
  bool sw[kSwitches] = {};
  bool sw_seen_on[kSwitches] = {};   // for the switch test: has it been pressed
  bool sw_seen_off[kSwitches] = {};  // and released since the test was reset
  int32_t enc_min[kMotors] = {}, enc_max[kMotors] = {};  // encoder test: range seen
  bool driver_fault = false, vm_ok = false;
  float vm = NAN;
  char fault[64] = "";
  // progress of the current long-running job
  int busy_joint = -1;
  char phase[32] = "";
};

class Robot {
 public:
  Robot(Hal& hal, RobotConfig& cfg) : hal_(hal), cfg_(cfg) {}

  void update(float dt);  // call at 1 kHz

  // Commands; each returns false (with lastError()) if not allowed now.
  bool enable();
  void disable();
  void stop();                       // decelerate everything, stay enabled
  bool resetFault();                 // clear fault / E-stop latch (needs VM)
  bool jog(int joint, float speed);  // speed as a fraction of vmax, -1..1
  bool moveTo(const float* target, const bool* use, float speed);  // degrees, speed 0..1
  bool home(int joint);              // -1 = all, in cfg.home_order
  bool calibrate(int joint);         // -1 = all non-continuous joints
  bool testMotor(int motor);
  bool selfTest();
  bool applyCalibration(int joint);  // copy calib result into the config
  void flipMotorDir(int motor);
  void invertJoint(int joint);       // flips encoder + motor sign together
  void setZeroHere(int joint);       // current position = home_pos, marks homed
  void setCountsPerUnit(int joint, float cpu);
  void resetSwitchTest();
  void resetEncoderTest();

  const Status& status() const { return st_; }
  const MotorTestResult& motorTest(int m) const { return mtest_[m]; }
  const CalibResult& calibResult(int j) const { return calib_[j]; }
  TestKind testKind() const { return test_kind_; }
  const char* lastError() const { return err_; }

  // Joint <-> motor count mapping (exposed for tests).
  void motorsToJoints(const float* m, float* j) const;
  void jointsToMotors(const float* j, float* m) const;

 private:
  enum class Phase : uint8_t {
    kNone,
    // homing
    kHomeLeaveSwitch, kHomeSeek, kHomePass, kHomeSettle, kHomeGripClose, kHomeGripOpen,
    // calibration
    kCalHome, kCalToMin, kCalToMax, kCalReturn,
    // motor test
    kTestFwd, kTestPause, kTestRev, kTestDone,
  };

  void readInputs(float dt);
  void runJob(float dt);
  void control(float dt);
  void fault(const char* why);
  bool canMove();
  void startJoint(int joint);  // begin homing/calibrating one joint
  void finishJoint();
  void nextQueued();
  float jointCounts(int joint) const;            // joint counts from actual motor counts
  float unitToCounts(int joint, float u) const;  // joint position -> joint counts
  float countsToUnit(int joint, float c) const;
  void holdHere();
  bool blocked(int joint, float dt);            // stall / hard-stop detector
  void setPhase(Phase p, const char* name);

  Hal& hal_;
  RobotConfig& cfg_;
  Status st_;
  char err_[64] = "";

  // control state (joint counts space for targets, motor counts for PID)
  float jtarget_[kJoints] = {};   // joint counts
  float jvel_[kJoints] = {};      // joint counts/s commanded (jog/homing)
  float mpos_[kMotors] = {};      // motor counts (signed by enc_dir)
  float mprev_[kMotors] = {};
  float mvel_[kMotors] = {};      // filtered motor velocity counts/s
  float integ_[kMotors] = {};
  float zero_[kJoints] = {};      // joint counts at home_pos
  uint32_t jog_deadline_[kJoints] = {};
  float fe_ms_[kMotors] = {};     // time spent over the following-error limit
  float stall_ms_[kJoints] = {};
  bool open_loop_ = false;        // motor test drives duty directly
  float open_duty_[kMotors] = {};

  // trajectory
  bool traj_active_ = false;
  float traj_t_ = 0, traj_T_ = 0, traj_from_[kJoints] = {}, traj_to_[kJoints] = {};

  // jobs
  Phase phase_ = Phase::kNone;
  float phase_t_ = 0;
  int job_joint_ = -1;
  int queue_[kJoints + 1] = {};
  int queue_len_ = 0, queue_pos_ = 0;
  bool job_is_calib_ = false;
  TestKind test_kind_ = TestKind::kNone;
  int test_motor_ = -1;
  int32_t test_start_ = 0;
  MotorTestResult mtest_[kMotors];
  CalibResult calib_[kJoints];
  float latch_ = 0;  // seek: set once the search has reversed direction
  bool first_ = true;
  float seek_start_ = 0;  // joint counts where the current switch search began
  float oc_ms_[kMotors] = {};
  bool sw_prev_[kSwitches] = {};
};

}  // namespace scorbot
