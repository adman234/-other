#include "robot.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

namespace scorbot {

namespace {
float Clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
float Sign(float v) { return v > 0 ? 1.f : (v < 0 ? -1.f : 0.f); }

// Normalised trapezoid: 25 % accelerate, 50 % cruise, 25 % decelerate.
constexpr float kAccelFrac = 0.25f;
float Profile(float x) {
  x = Clamp(x, 0.f, 1.f);
  const float ta = kAccelFrac, vpk = 1.f / (1.f - ta);
  if (x < ta) return 0.5f * vpk / ta * x * x;
  if (x > 1.f - ta) {
    float r = 1.f - x;
    return 1.f - 0.5f * vpk / ta * r * r;
  }
  return 0.5f * vpk * ta + vpk * (x - ta);
}
}  // namespace

const char* ModeName(Mode m) {
  switch (m) {
    case Mode::kDisabled: return "disabled";
    case Mode::kIdle: return "idle";
    case Mode::kMoving: return "moving";
    case Mode::kJog: return "jog";
    case Mode::kHoming: return "homing";
    case Mode::kCalibrating: return "calibrating";
    case Mode::kTest: return "test";
    case Mode::kFault: return "fault";
    case Mode::kEStop: return "estop";
  }
  return "?";
}

const char* ResultName(Result r) {
  switch (r) {
    case Result::kPending: return "pending";
    case Result::kPass: return "pass";
    case Result::kWarn: return "warn";
    case Result::kFail: return "fail";
    case Result::kSkipped: return "skipped";
  }
  return "?";
}

// ---------------------------------------------------------------------------
// Mapping. Motors 3 and 4 (0-based) drive the differential wrist:
// pitch = (m3 + m4) / 2, roll = (m3 - m4) / 2.
// ---------------------------------------------------------------------------
void Robot::motorsToJoints(const float* m, float* j) const {
  j[kBase] = m[0];
  j[kShoulder] = m[1];
  j[kElbow] = m[2];
  j[kPitch] = 0.5f * (m[3] + m[4]);
  j[kRoll] = 0.5f * (m[3] - m[4]);
  j[kGripper] = m[5];
}

void Robot::jointsToMotors(const float* j, float* m) const {
  m[0] = j[kBase];
  m[1] = j[kShoulder];
  m[2] = j[kElbow];
  m[3] = j[kPitch] + j[kRoll];
  m[4] = j[kPitch] - j[kRoll];
  m[5] = j[kGripper];
}

float Robot::jointCounts(int joint) const {
  float jc[kJoints];
  motorsToJoints(mpos_, jc);
  return jc[joint];
}

float Robot::unitToCounts(int j, float u) const {
  return zero_[j] + (u - cfg_.joint[j].home_pos) * cfg_.joint[j].counts_per_unit;
}

float Robot::countsToUnit(int j, float c) const {
  return cfg_.joint[j].home_pos + (c - zero_[j]) / cfg_.joint[j].counts_per_unit;
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------
void Robot::update(float dt) {
  readInputs(dt);
  if (st_.mode == Mode::kHoming || st_.mode == Mode::kCalibrating || st_.mode == Mode::kTest) runJob(dt);
  control(dt);
  float jc[kJoints];
  motorsToJoints(mpos_, jc);
  for (int j = 0; j < kJoints; ++j) {
    st_.pos[j] = countsToUnit(j, jc[j]);
    st_.target[j] = countsToUnit(j, jtarget_[j]);
  }
}

void Robot::readInputs(float dt) {
  const bool first = first_;
  for (int m = 0; m < kMotors; ++m) {
    int32_t raw = hal_.encoder(m) * cfg_.motor[m].enc_dir;
    st_.counts[m] = raw;
    mpos_[m] = static_cast<float>(raw);
    if (first) {
      mprev_[m] = mpos_[m];
      st_.enc_min[m] = st_.enc_max[m] = raw;
    }
    float v = (mpos_[m] - mprev_[m]) / dt;
    mvel_[m] += 0.05f * (v - mvel_[m]);  // ~20 ms low-pass at 1 kHz
    mprev_[m] = mpos_[m];
    st_.enc_min[m] = std::min(st_.enc_min[m], raw);
    st_.enc_max[m] = std::max(st_.enc_max[m], raw);
    st_.current[m] = hal_.current(m);
  }
  if (first) {  // unhomed: current pose reads as home_pos
    float jc[kJoints];
    motorsToJoints(mpos_, jc);
    for (int j = 0; j < kJoints; ++j) zero_[j] = jtarget_[j] = jc[j];
    first_ = false;
  }
  for (int s = 0; s < kSwitches; ++s) {
    bool on = hal_.homeSwitch(s);
    st_.sw[s] = on;
    if (on) st_.sw_seen_on[s] = true;
    if (!on) st_.sw_seen_off[s] = true;
  }
  st_.vm_ok = hal_.motorPowerOk();
  st_.vm = hal_.motorVolts();
  st_.driver_fault = hal_.driverFault();

  if (st_.enabled) {
    if (!st_.vm_ok) {
      disable();
      st_.mode = Mode::kEStop;
      std::snprintf(st_.fault, sizeof(st_.fault), "motor power lost (E-stop?)");
      return;
    }
    if (st_.driver_fault) {
      fault("driver fault (nFAULT)");
      return;
    }
    for (int m = 0; m < kMotors; ++m) {
      if (!std::isnan(st_.current[m]) && st_.current[m] > cfg_.current_limit_a)
        oc_ms_[m] += dt * 1000.f;
      else
        oc_ms_[m] = 0;
      if (oc_ms_[m] > 50.f) {
        char buf[48];
        std::snprintf(buf, sizeof(buf), "over-current on motor %d", m + 1);
        fault(buf);
        return;
      }
    }
  }
}

void Robot::control(float dt) {
  if (!st_.enabled) {
    for (int m = 0; m < kMotors; ++m) {
      hal_.setMotor(m, 0);
      st_.duty[m] = 0;
      integ_[m] = 0;
    }
    return;
  }
  // Trajectory or velocity targets in joint counts.
  if (traj_active_) {
    traj_t_ += dt;
    float s = Profile(traj_t_ / traj_T_);
    for (int j = 0; j < kJoints; ++j) jtarget_[j] = traj_from_[j] + (traj_to_[j] - traj_from_[j]) * s;
    if (traj_t_ >= traj_T_) {
      traj_active_ = false;
      if (st_.mode == Mode::kMoving) st_.mode = Mode::kIdle;
    }
  }
  const uint32_t now = hal_.millis();
  const bool free_travel = st_.mode == Mode::kCalibrating || st_.mode == Mode::kHoming || st_.mode == Mode::kTest;
  bool any_jog = false;
  for (int j = 0; j < kJoints; ++j) {
    if (st_.mode == Mode::kJog && jvel_[j] != 0 && now > jog_deadline_[j]) jvel_[j] = 0;
    if (jvel_[j] != 0) {
      jtarget_[j] += jvel_[j] * dt;
      any_jog = true;
    }
    const JointConfig& jc = cfg_.joint[j];
    if (st_.homed[j] && !free_travel && !jc.continuous) {
      float lo = unitToCounts(j, jc.soft_min), hi = unitToCounts(j, jc.soft_max);
      if (lo > hi) std::swap(lo, hi);
      float clamped = Clamp(jtarget_[j], lo, hi);
      if (clamped != jtarget_[j]) {
        jtarget_[j] = clamped;
        jvel_[j] = 0;
      }
    }
  }
  if (st_.mode == Mode::kJog && !any_jog) st_.mode = Mode::kIdle;

  // Per-motor PID on motor counts.
  float mtarget[kMotors];
  jointsToMotors(jtarget_, mtarget);
  for (int m = 0; m < kMotors; ++m) {
    const MotorConfig& mc = cfg_.motor[m];
    const int j = (m == 3 || m == 4) ? kPitch : m;
    if (open_loop_ && m == test_motor_) {  // motor test: this motor runs open loop
      st_.duty[m] = open_duty_[m];
      hal_.setMotor(m, open_duty_[m] * mc.motor_dir);
      integ_[m] = 0;
      fe_ms_[m] = 0;
      continue;
    }
    float e = mtarget[m] - mpos_[m];
    if (std::fabs(e) < mc.i_zone)
      integ_[m] = Clamp(integ_[m] + e * dt, -0.5f / std::max(mc.ki, 1e-6f), 0.5f / std::max(mc.ki, 1e-6f));
    else
      integ_[m] = 0;
    float u = mc.kp * e + mc.ki * integ_[m] - mc.kd * mvel_[m];
    float cap = mc.max_duty;
    if (st_.mode == Mode::kCalibrating || st_.mode == Mode::kHoming) cap = std::min(cap, cfg_.calib_duty);
    float out = 0;
    if (std::fabs(e) > 2.f || std::fabs(u) > 0.05f)  // small deadzone so it doesn't buzz at rest
      out = Sign(u) * std::min(cap, mc.min_duty + (1.f - mc.min_duty) * std::fabs(u));
    st_.duty[m] = out;
    hal_.setMotor(m, out * mc.motor_dir);

    // Following-error fault (homing/calibration use the stall detector instead).
    const float limit = cfg_.joint[j].follow_err * cfg_.joint[j].counts_per_unit;
    if (!free_travel && std::fabs(e) > limit) {
      fe_ms_[m] += dt * 1000.f;
      if (fe_ms_[m] > 100.f) {
        char buf[64];
        std::snprintf(buf, sizeof(buf), "following error on motor %d (jam or encoder?)", m + 1);
        fault(buf);
        return;
      }
    } else {
      fe_ms_[m] = 0;
    }
  }
}

// Joint is pushing but not moving: hard stop, jam or (homing) missing switch.
bool Robot::blocked(int j, float dt) {
  float jvel[kJoints];
  motorsToJoints(mvel_, jvel);
  const float cpu = cfg_.joint[j].counts_per_unit;
  const float err = std::fabs(jtarget_[j] - jointCounts(j)) / cpu;
  const float vel = std::fabs(jvel[j]) / cpu;
  if (err > 1.5f && vel < 0.3f * cfg_.joint[j].home_speed)
    stall_ms_[j] += dt * 1000.f;
  else
    stall_ms_[j] = 0;
  return stall_ms_[j] >= cfg_.joint[j].stall_ms;
}

void Robot::holdHere() {
  float jc[kJoints];
  motorsToJoints(mpos_, jc);
  for (int j = 0; j < kJoints; ++j) {
    jtarget_[j] = jc[j];
    jvel_[j] = 0;
  }
  traj_active_ = false;
  for (int m = 0; m < kMotors; ++m) integ_[m] = 0;
}

void Robot::fault(const char* why) {
  hal_.enableDrivers(false);
  st_.enabled = false;
  open_loop_ = false;
  traj_active_ = false;
  phase_ = Phase::kNone;
  queue_len_ = 0;
  st_.busy_joint = -1;
  st_.mode = Mode::kFault;
  std::snprintf(st_.fault, sizeof(st_.fault), "%s", why);
  for (int j = 0; j < kJoints; ++j) jvel_[j] = 0;
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------
bool Robot::enable() {
  if (st_.mode == Mode::kFault || st_.mode == Mode::kEStop) {
    std::snprintf(err_, sizeof(err_), "clear the fault first");
    return false;
  }
  if (!hal_.motorPowerOk()) {
    std::snprintf(err_, sizeof(err_), "no motor power (E-stop open?)");
    return false;
  }
  holdHere();
  st_.enabled = true;
  hal_.enableDrivers(true);
  if (st_.mode == Mode::kDisabled) st_.mode = Mode::kIdle;
  return true;
}

void Robot::disable() {
  hal_.enableDrivers(false);
  st_.enabled = false;
  open_loop_ = false;
  traj_active_ = false;
  phase_ = Phase::kNone;
  queue_len_ = 0;
  st_.busy_joint = -1;
  st_.phase[0] = 0;
  for (int j = 0; j < kJoints; ++j) jvel_[j] = 0;
  if (st_.mode != Mode::kFault && st_.mode != Mode::kEStop) st_.mode = Mode::kDisabled;
}

void Robot::stop() {
  if (!st_.enabled) return;
  open_loop_ = false;
  phase_ = Phase::kNone;
  queue_len_ = 0;
  st_.busy_joint = -1;
  st_.phase[0] = 0;
  holdHere();
  st_.mode = Mode::kIdle;
}

bool Robot::resetFault() {
  if (!hal_.motorPowerOk()) {
    std::snprintf(err_, sizeof(err_), "motor power still off (E-stop open?)");
    return false;
  }
  if (st_.mode == Mode::kFault || st_.mode == Mode::kEStop) st_.mode = Mode::kDisabled;
  st_.fault[0] = 0;
  return true;
}

bool Robot::canMove() {
  if (!st_.enabled) {
    std::snprintf(err_, sizeof(err_), "not enabled");
    return false;
  }
  if (st_.mode == Mode::kHoming || st_.mode == Mode::kCalibrating || st_.mode == Mode::kTest) {
    std::snprintf(err_, sizeof(err_), "busy (%s)", ModeName(st_.mode));
    return false;
  }
  return true;
}

bool Robot::jog(int j, float speed) {
  if (j < 0 || j >= kJoints || !canMove()) return false;
  if (st_.mode == Mode::kMoving) {
    holdHere();
  }
  speed = Clamp(speed, -1.f, 1.f);
  jvel_[j] = speed * cfg_.joint[j].vmax * cfg_.joint[j].counts_per_unit;
  jog_deadline_[j] = hal_.millis() + cfg_.jog_timeout_ms;
  if (speed != 0) st_.mode = Mode::kJog;
  return true;
}

bool Robot::moveTo(const float* target, const bool* use, float speed) {
  if (!canMove()) return false;
  speed = Clamp(speed, 0.05f, 1.f);
  float T = 0.05f;
  for (int j = 0; j < kJoints; ++j) {
    traj_from_[j] = jtarget_[j];
    jvel_[j] = 0;
    traj_to_[j] = jtarget_[j];
    if (!use[j]) continue;
    const JointConfig& jc = cfg_.joint[j];
    float u = target[j];
    if (st_.homed[j] && !jc.continuous) u = Clamp(u, std::min(jc.soft_min, jc.soft_max), std::max(jc.soft_min, jc.soft_max));
    traj_to_[j] = unitToCounts(j, u);
    float d = std::fabs(traj_to_[j] - traj_from_[j]) / jc.counts_per_unit;  // units
    float v = jc.vmax * speed, a = jc.amax * speed;
    float t1 = d / ((1.f - kAccelFrac) * v);
    float t2 = std::sqrt(d / ((1.f - kAccelFrac) * kAccelFrac * a));
    T = std::max(T, std::max(t1, t2));
  }
  traj_t_ = 0;
  traj_T_ = T;
  traj_active_ = true;
  st_.mode = Mode::kMoving;
  return true;
}

void Robot::setPhase(Phase p, const char* name) {
  phase_ = p;
  phase_t_ = 0;
  std::snprintf(st_.phase, sizeof(st_.phase), "%s", name);
}

bool Robot::home(int joint) {
  if (!canMove()) return false;
  queue_len_ = queue_pos_ = 0;
  if (joint < 0) {
    for (int k = 0; k < kJoints; ++k) queue_[queue_len_++] = cfg_.home_order[k];
  } else if (joint < kJoints) {
    queue_[queue_len_++] = joint;
  } else {
    return false;
  }
  job_is_calib_ = false;
  st_.mode = Mode::kHoming;
  holdHere();
  nextQueued();
  return true;
}

bool Robot::calibrate(int joint) {
  if (!canMove()) return false;
  queue_len_ = queue_pos_ = 0;
  for (int k = 0; k < kJoints; ++k) {
    int j = cfg_.home_order[k];
    if (joint >= 0 && j != joint) continue;
    if (cfg_.joint[j].continuous) {
      calib_[j] = CalibResult();
      calib_[j].result = Result::kSkipped;
      std::snprintf(calib_[j].note, sizeof(calib_[j].note), "no hard stops: use the 360 deg method");
      continue;
    }
    calib_[j] = CalibResult();
    queue_[queue_len_++] = j;
  }
  if (queue_len_ == 0) {
    std::snprintf(err_, sizeof(err_), "nothing to calibrate");
    return false;
  }
  job_is_calib_ = true;
  st_.mode = Mode::kCalibrating;
  holdHere();
  nextQueued();
  return true;
}

void Robot::nextQueued() {
  if (queue_pos_ >= queue_len_) {
    queue_len_ = 0;
    st_.busy_joint = -1;
    phase_ = Phase::kNone;
    st_.phase[0] = 0;
    holdHere();
    open_loop_ = false;
    test_motor_ = -1;
    st_.mode = st_.enabled ? Mode::kIdle : Mode::kDisabled;
    return;
  }
  int item = queue_[queue_pos_++];
  if (st_.mode == Mode::kTest) {
    test_motor_ = item;
    mtest_[item] = MotorTestResult();
    st_.busy_joint = item;
    test_start_ = st_.counts[item];
    for (int m = 0; m < kMotors; ++m) open_duty_[m] = 0;
    open_loop_ = true;
    setPhase(Phase::kTestFwd, "forward pulse");
    return;
  }
  startJoint(item);
}

void Robot::startJoint(int j) {
  job_joint_ = j;
  st_.busy_joint = j;
  stall_ms_[j] = 0;
  holdHere();
  const JointConfig& jc = cfg_.joint[j];
  if (job_is_calib_ && st_.homed[j]) {
    setPhase(Phase::kCalToMin, "to min stop");
    return;
  }
  if (!jc.has_switch) {
    setPhase(Phase::kHomeGripClose, "closing to stop");
    return;
  }
  if (j < kSwitches && st_.sw[j])
    setPhase(Phase::kHomeLeaveSwitch, "leaving switch");
  else
    setPhase(Phase::kHomeSeek, "seeking switch");
  latch_ = 0;  // used as "already reversed" flag during seek
  seek_start_ = jointCounts(j);
}

void Robot::finishJoint() {
  int j = job_joint_;
  jvel_[j] = 0;
  stall_ms_[j] = 0;
  holdHere();
  nextQueued();
}

void Robot::runJob(float dt) {
  if (phase_ == Phase::kNone) return;
  phase_t_ += dt;
  const int j = job_joint_;

  // ---- motor test (open loop) ----
  if (st_.mode == Mode::kTest) {
    const int m = test_motor_;
    MotorTestResult& r = mtest_[m];
    const float tl = cfg_.test_ms / 1000.f;
    if (!std::isnan(st_.current[m])) r.peak_current = std::isnan(r.peak_current) ? st_.current[m] : std::max(r.peak_current, st_.current[m]);
    switch (phase_) {
      case Phase::kTestFwd:
        open_duty_[m] = cfg_.test_duty;
        if (phase_t_ >= tl) {
          r.delta_fwd = st_.counts[m] - test_start_;
          open_duty_[m] = 0;
          setPhase(Phase::kTestPause, "pause");
        }
        break;
      case Phase::kTestPause:
        if (phase_t_ >= 0.25f) {
          test_start_ = st_.counts[m];
          setPhase(Phase::kTestRev, "reverse pulse");
        }
        break;
      case Phase::kTestRev:
        open_duty_[m] = -cfg_.test_duty;
        if (phase_t_ >= tl) {
          r.delta_rev = st_.counts[m] - test_start_;
          open_duty_[m] = 0;
          setPhase(Phase::kTestDone, "settle");
        }
        break;
      case Phase::kTestDone:
        if (phase_t_ >= 0.25f) {
          const int thr = 15;
          const bool f = std::abs(r.delta_fwd) >= thr, b = std::abs(r.delta_rev) >= thr;
          const bool lowI = !std::isnan(r.peak_current) && r.peak_current < 0.05f;
          if (!f && !b) {
            r.result = Result::kFail;
            std::snprintf(r.note, sizeof(r.note), "%s",
                          lowI ? "no current: check motor wiring / driver" : "no encoder counts: jammed, or encoder fault");
          } else if (r.delta_fwd > 0 && r.delta_rev < 0 && f && b) {
            r.result = Result::kPass;
            std::snprintf(r.note, sizeof(r.note), "direction and encoder OK");
          } else if (r.delta_fwd < 0 && r.delta_rev > 0 && f && b) {
            r.result = Result::kWarn;
            std::snprintf(r.note, sizeof(r.note), "motor and encoder disagree: tap Flip motor");
          } else {
            r.result = Result::kWarn;
            std::snprintf(r.note, sizeof(r.note), "moved one way only: at a stop, or weak encoder channel");
          }
          nextQueued();
        }
        break;
      default:
        break;
    }
    return;
  }

  const JointConfig& jc = cfg_.joint[j];
  const float cpu = jc.counts_per_unit;
  const float seek = jc.home_speed * cpu;  // counts/s
  const bool on = j < kSwitches ? st_.sw[j] : false;

  switch (phase_) {
    case Phase::kHomeLeaveSwitch:
      jvel_[j] = -jc.home_dir * seek;
      if (on) phase_t_ = 0;  // need 0.3 s clear of the switch
      if (phase_t_ > 0.3f) {
        seek_start_ = jointCounts(j);
        setPhase(Phase::kHomeSeek, "seeking switch");
      }
      if (blocked(j, dt)) {
        fault("homing: stuck while leaving the switch");
      }
      break;
    case Phase::kHomeSeek: {
      jvel_[j] = (latch_ != 0 ? -jc.home_dir : jc.home_dir) * seek;
      // Continuous joints (roll) have no stops: give up on a direction after 1.5 turns (counts/deg may still be an estimate).
      const bool too_far = jc.continuous && std::fabs(jointCounts(j) - seek_start_) > 540.f * cpu;
      if (on) {
        setPhase(Phase::kHomePass, "finding switch edge");
      } else if (blocked(j, dt) || too_far) {
        if (latch_ == 0) {  // hit a stop without seeing the switch: search the other way once
          latch_ = 1;
          stall_ms_[j] = 0;
          holdHere();
          seek_start_ = jointCounts(j);
        } else {
          char buf[64];
          std::snprintf(buf, sizeof(buf), "homing %s: switch not found", jc.name);
          fault(buf);
        }
      }
      break;
    }
    case Phase::kHomePass: {
      const float dir = Sign(jvel_[j]) != 0 ? Sign(jvel_[j]) : jc.home_dir;
      jvel_[j] = dir * seek * 0.3f;  // creep across the switch
      if (!on) {
        // Far edge of the switch, approached the same way every time.
        zero_[j] = jointCounts(j);
        st_.homed[j] = true;
        if (job_is_calib_) calib_[j].home_counts = static_cast<int32_t>(zero_[j]);
        jvel_[j] = 0;
        holdHere();
        setPhase(Phase::kHomeSettle, "settling");
      } else if (blocked(j, dt)) {
        fault("homing: stuck on the switch");
      }
      break;
    }
    case Phase::kHomeSettle:
      if (phase_t_ > 0.3f) {
        if (job_is_calib_)
          setPhase(Phase::kCalToMin, "to min stop");
        else
          finishJoint();
      }
      break;
    case Phase::kHomeGripClose:
      jvel_[j] = -seek;
      if (blocked(j, dt)) {
        jvel_[j] = 0;
        zero_[j] = jointCounts(j) - jc.home_pos * cpu;  // closed = 0 mm
        st_.homed[j] = true;
        if (job_is_calib_) {
          calib_[j].home_counts = static_cast<int32_t>(zero_[j]);
          holdHere();
          setPhase(Phase::kCalToMin, "to min stop");
        } else {
          holdHere();
          setPhase(Phase::kHomeGripOpen, "opening");
        }
      }
      break;
    case Phase::kHomeGripOpen: {
      const float goal = unitToCounts(j, cfg_.gripper_open_mm);
      jvel_[j] = seek;
      if (jtarget_[j] >= goal) {
        jtarget_[j] = goal;
        finishJoint();
      }
      break;
    }
    case Phase::kCalToMin:
      jvel_[j] = -seek;
      if (blocked(j, dt)) {
        calib_[j].min_counts = static_cast<int32_t>(jointCounts(j));
        stall_ms_[j] = 0;
        holdHere();
        setPhase(Phase::kCalToMax, "to max stop");
      }
      break;
    case Phase::kCalToMax:
      if (phase_t_ < 0.3f) break;  // let it relax off the stop
      jvel_[j] = seek;
      if (blocked(j, dt)) {
        CalibResult& r = calib_[j];
        r.max_counts = static_cast<int32_t>(jointCounts(j));
        const float span = static_cast<float>(r.max_counts - r.min_counts);
        const float range = jc.spec_max - jc.spec_min;
        if (span < 50.f) {
          r.result = Result::kFail;
          std::snprintf(r.note, sizeof(r.note), "span only %.0f counts", span);
        } else {
          r.counts_per_unit = span / range;
          r.home_pos = jc.spec_min + (zero_[j] - r.min_counts) / r.counts_per_unit;
          const float margin = j == kGripper ? 1.f : 3.f;
          r.soft_min = jc.spec_min + margin;
          r.soft_max = jc.spec_max - margin;
          const float ratio = r.counts_per_unit / jc.counts_per_unit;
          r.result = (ratio > 0.5f && ratio < 2.f) || !jc.cpu_calibrated ? Result::kPass : Result::kWarn;
          std::snprintf(r.note, sizeof(r.note), "%.1f counts/%s over %.0f %s", r.counts_per_unit,
                        j == kGripper ? "mm" : "deg", range, j == kGripper ? "mm" : "deg");
        }
        stall_ms_[j] = 0;
        holdHere();
        setPhase(Phase::kCalReturn, "returning home");
      }
      break;
    case Phase::kCalReturn: {
      const float goal = zero_[j];
      const float d = goal - jtarget_[j];
      if (std::fabs(d) <= seek * dt * 2.f) {
        jtarget_[j] = goal;
        jvel_[j] = 0;
        if (phase_t_ > 0.2f && std::fabs(goal - jointCounts(j)) < 2.f * cpu) finishJoint();
      } else {
        jvel_[j] = Sign(d) * seek * 2.f;
        phase_t_ = 0;
      }
      if (phase_t_ > 5.f) finishJoint();
      break;
    }
    default:
      break;
  }
}

bool Robot::testMotor(int motor) {
  if (motor < 0 || motor >= kMotors) return false;
  if (!canMove()) return false;
  holdHere();
  queue_len_ = queue_pos_ = 0;
  queue_[queue_len_++] = motor;
  test_kind_ = TestKind::kMotor;
  st_.mode = Mode::kTest;
  nextQueued();
  return true;
}

bool Robot::selfTest() {
  if (!canMove()) return false;
  holdHere();
  queue_len_ = queue_pos_ = 0;
  for (int m = 0; m < kMotors; ++m) {
    queue_[queue_len_++] = m;
    mtest_[m] = MotorTestResult();
  }
  test_kind_ = TestKind::kSelfTest;
  st_.mode = Mode::kTest;
  nextQueued();
  return true;
}

bool Robot::applyCalibration(int j) {
  if (j < 0 || j >= kJoints || (calib_[j].result != Result::kPass && calib_[j].result != Result::kWarn)) {
    std::snprintf(err_, sizeof(err_), "no calibration result for that joint");
    return false;
  }
  JointConfig& jc = cfg_.joint[j];
  const CalibResult& r = calib_[j];
  jc.counts_per_unit = r.counts_per_unit;
  jc.cpu_calibrated = true;
  jc.home_pos = r.home_pos;
  jc.soft_min = r.soft_min;
  jc.soft_max = r.soft_max;
  return true;
}

void Robot::flipMotorDir(int m) {
  if (m >= 0 && m < kMotors) cfg_.motor[m].motor_dir = -cfg_.motor[m].motor_dir;
}

void Robot::invertJoint(int j) {
  if (j < 0 || j >= kJoints) return;
  int ms[2] = {j, -1};
  if (j == kPitch || j == kRoll) {
    // Inverting a wrist joint swaps the two wrist motors' roles; flip both encoders and drives.
    ms[0] = 3;
    ms[1] = 4;
  }
  for (int m : ms) {
    if (m < 0) continue;
    cfg_.motor[m].enc_dir = -cfg_.motor[m].enc_dir;
    cfg_.motor[m].motor_dir = -cfg_.motor[m].motor_dir;
  }
  st_.homed[j] = false;
  if (j == kPitch || j == kRoll) st_.homed[kPitch] = st_.homed[kRoll] = false;
}

void Robot::setZeroHere(int j) {
  if (j < 0 || j >= kJoints) return;
  zero_[j] = jointCounts(j);
  jtarget_[j] = zero_[j];
  st_.homed[j] = true;
}

void Robot::setCountsPerUnit(int j, float cpu) {
  if (j < 0 || j >= kJoints || cpu <= 0) return;
  // keep the current position reading continuous
  const float pos = countsToUnit(j, jointCounts(j));
  cfg_.joint[j].counts_per_unit = cpu;
  cfg_.joint[j].cpu_calibrated = true;
  zero_[j] = jointCounts(j) - (pos - cfg_.joint[j].home_pos) * cpu;
}

void Robot::resetSwitchTest() {
  for (int s = 0; s < kSwitches; ++s) st_.sw_seen_on[s] = st_.sw_seen_off[s] = false;
}

void Robot::resetEncoderTest() {
  for (int m = 0; m < kMotors; ++m) st_.enc_min[m] = st_.enc_max[m] = st_.counts[m];
}

}  // namespace scorbot
