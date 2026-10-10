// Simple physics simulator of the ER-4u for host tests: DC motors with static
// friction, joint hard stops, home switches and the differential wrist.
#pragma once
#include <algorithm>
#include <cmath>

#include "../main/core/robot.h"

namespace scorbot {

struct SimArm : Hal {
  // "true" machine parameters the firmware has to discover
  float true_cpu[kJoints] = {120.f, 150.f, 130.f, 90.f, 90.f, 110.f};  // counts per deg / mm
  float stop_min[kJoints] = {-155, -35, -130, -130, -1e9f, 0};          // degrees / mm
  float stop_max[kJoints] = {155, 130, 130, 130, 1e9f, 75};
  float switch_at[kJoints] = {12, 40, -20, 5, 33, 0};                   // switch centre, degrees
  float switch_width = 2.0f;                                            // degrees
  int motor_polarity[kMotors] = {1, 1, 1, 1, 1, 1};                     // -1 = wired backwards
  int enc_polarity[kMotors] = {1, 1, 1, 1, 1, 1};
  float friction = 0.25f;           // duty needed to start moving
  float max_speed_cps = 6000.f;     // counts/s at full duty
  bool vm = true, fault = false, jammed[kMotors] = {};
  float start_deg[kJoints] = {20, 60, -40, 10, 0, 30};

  float mcount[kMotors] = {};  // motor shaft position in counts
  float duty[kMotors] = {};
  bool enabled = false, stalled[kMotors] = {};
  uint32_t t_ms = 0;
  float t_acc = 0;

  SimArm() {
    float jc[kJoints];
    for (int j = 0; j < kJoints; ++j) jc[j] = start_deg[j] * true_cpu[j];
    ToMotors(jc, mcount);
  }
  static void ToJoints(const float* m, float* j) {
    j[0] = m[0]; j[1] = m[1]; j[2] = m[2]; j[3] = 0.5f * (m[3] + m[4]); j[4] = 0.5f * (m[3] - m[4]); j[5] = m[5];
  }
  static void ToMotors(const float* j, float* m) {
    m[0] = j[0]; m[1] = j[1]; m[2] = j[2]; m[3] = j[3] + j[4]; m[4] = j[3] - j[4]; m[5] = j[5];
  }
  float jointDeg(int j) const {
    float jc[kJoints];
    ToJoints(mcount, jc);
    return jc[j] / true_cpu[j];
  }

  void step(float dt) {
    t_acc += dt * 1000.f;
    while (t_acc >= 1.f) { ++t_ms; t_acc -= 1.f; }
    float before[kMotors];
    std::copy(mcount, mcount + kMotors, before);
    for (int m = 0; m < kMotors; ++m) {
      float d = (enabled && vm) ? duty[m] * motor_polarity[m] : 0.f;
      float a = std::fabs(d);
      float v = a > friction ? (a - friction) / (1.f - friction) * max_speed_cps : 0.f;
      if (!jammed[m]) mcount[m] += (d > 0 ? v : -v) * dt;
    }
    // hard stops in joint space
    float jc[kJoints];
    ToJoints(mcount, jc);
    for (int j = 0; j < kJoints; ++j) {
      jc[j] = std::max(stop_min[j] * true_cpu[j], std::min(stop_max[j] * true_cpu[j], jc[j]));
    }
    ToMotors(jc, mcount);
    for (int m = 0; m < kMotors; ++m) {
      float d = (enabled && vm) ? std::fabs(duty[m]) : 0.f;
      stalled[m] = d > friction && std::fabs(mcount[m] - before[m]) < 1e-3f;
    }
  }

  // Hal
  int32_t encoder(int m) override { return static_cast<int32_t>(std::lround(mcount[m])) * enc_polarity[m]; }
  void setMotor(int m, float d) override { duty[m] = d; }
  void enableDrivers(bool on) override { enabled = on; }
  bool homeSwitch(int s) override {
    float d = jointDeg(s) - switch_at[s];
    if (s == kRoll) d = std::remainder(d, 360.f);  // roll cam trips once per turn
    return std::fabs(d) < switch_width / 2;
  }
  bool driverFault() override { return fault; }
  bool motorPowerOk() override { return vm; }
  float current(int m) override {
    if (!enabled || !vm) return 0.f;
    return std::fabs(duty[m]) * (stalled[m] ? 2.0f : 0.6f);
  }
  float motorVolts() override { return vm ? 12.1f : 0.2f; }
  uint32_t millis() override { return t_ms; }
};

}  // namespace scorbot
