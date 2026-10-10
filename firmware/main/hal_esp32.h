// ESP32 implementation of scorbot::Hal: PCNT encoders, LEDC PWM, TCA9555
// (direction, switches, fault, E-stop sense) and two ADS1115s (current, VM).
#pragma once
#include <atomic>

#include "core/robot.h"
#include "driver/i2c_master.h"
#include "driver/pulse_cnt.h"
#include "esp_err.h"

class Esp32Hal : public scorbot::Hal {
 public:
  esp_err_t begin();
  // Slow I/O loop (I2C): run in its own task. Never returns.
  void ioLoop();

  int32_t encoder(int motor) override;
  void setMotor(int motor, float duty) override;
  void enableDrivers(bool on) override;
  bool homeSwitch(int sw) override;
  bool driverFault() override;
  bool motorPowerOk() override;
  float current(int motor) override;
  float motorVolts() override;
  uint32_t millis() override;

  bool hasExpander() const { return tca_ok_; }
  bool hasAdc(int i) const { return ads_ok_[i]; }
  void setLedPattern(int pattern) { led_pattern_ = pattern; }  // 0 off, 1 on, 2 slow, 3 fast

 private:
  esp_err_t initEncoders();
  esp_err_t initPwm();
  void initI2c();
  bool tcaWrite(uint8_t reg, uint8_t val);
  bool tcaRead(uint8_t reg, uint8_t* val);
  bool adsStart(int chip, int ch);
  bool adsRead(int chip, float* volts);

  pcnt_unit_handle_t pcnt_[6] = {};
  i2c_master_bus_handle_t bus_ = nullptr;
  i2c_master_dev_handle_t tca_ = nullptr;
  i2c_master_dev_handle_t ads_[2] = {};
  bool tca_ok_ = false;
  bool ads_ok_[2] = {};
  std::atomic<uint8_t> ph_want_{0};
  std::atomic<uint8_t> ph_applied_{0};
  std::atomic<uint8_t> port1_{0xBF};  // until read: switches released, no fault, VM absent
  std::atomic<float> cur_[6];
  std::atomic<float> vm_{NAN};
  std::atomic<int> led_pattern_{2};
  bool drivers_on_ = false;
};
