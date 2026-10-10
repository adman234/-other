// Pin map: must match hardware/scorbot-controller/generator/design.py.
// Works on an ESP32-DevKitC with either a WROOM-32D or a WROOM-32E module.
#pragma once
#include "driver/gpio.h"

namespace board {

// Encoder A/B per motor (P0/P1 on the DB50). 34-39 are input-only; the board
// has 4.7k pull-ups to 3.3 V on every encoder line.
constexpr gpio_num_t kEncA[6] = {GPIO_NUM_36, GPIO_NUM_34, GPIO_NUM_32, GPIO_NUM_25, GPIO_NUM_27, GPIO_NUM_4};
constexpr gpio_num_t kEncB[6] = {GPIO_NUM_39, GPIO_NUM_35, GPIO_NUM_33, GPIO_NUM_26, GPIO_NUM_13, GPIO_NUM_23};

// DRV8874 EN (PWM) per motor. 14/15 glitch at boot; harmless while nSLEEP is low.
constexpr gpio_num_t kPwm[6] = {GPIO_NUM_16, GPIO_NUM_17, GPIO_NUM_18, GPIO_NUM_19, GPIO_NUM_14, GPIO_NUM_15};

constexpr gpio_num_t kNSleep = GPIO_NUM_12;  // all drivers; 10k pull-down keeps them off at boot
constexpr gpio_num_t kSda = GPIO_NUM_21;
constexpr gpio_num_t kScl = GPIO_NUM_22;
constexpr gpio_num_t kExpanderInt = GPIO_NUM_5;
constexpr gpio_num_t kStatusLed = GPIO_NUM_2;

constexpr uint8_t kTcaAddr = 0x20;              // TCA9555: P00-P05 = PH1-6
constexpr uint8_t kAdsAddr[2] = {0x48, 0x49};   // ADS1115 #1: CS1-4, #2: CS5, CS6, VM/5.7, GND

// TCA9555 port 1 bits
constexpr int kSwitchBit0 = 0;  // P10-P14: home switches 1-5, pressed = low
constexpr int kFaultBit = 5;    // P15: nFAULT (wired-OR), low = fault
constexpr int kVmBit = 6;       // P16: VM present (22k/10k divider), high = motor power on

constexpr float kCsVoltsPerAmp = 1.12f;  // DRV8874 450 uA/A x 2.49k on the Pololu carrier
constexpr float kVmDivider = 5.7f;       // 47k / 10k

}  // namespace board
