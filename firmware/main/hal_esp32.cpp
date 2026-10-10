#include "hal_esp32.h"

#include <cmath>

#include "board.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "hal";

namespace {
constexpr ledc_mode_t kLedcMode = LEDC_HIGH_SPEED_MODE;
constexpr ledc_timer_bit_t kLedcBits = LEDC_TIMER_11_BIT;  // 80 MHz / 20 kHz = 4000 > 2048
constexpr uint32_t kLedcMax = (1u << 11) - 1;
constexpr int kPcntLimit = 30000;

// ADS1115 config word: single shot, AINx vs GND, +/-4.096 V, 860 SPS, comparator off.
uint16_t AdsConfig(int ch) {
  return 0x8000 | static_cast<uint16_t>((4 + ch) << 12) | (1u << 9) | (1u << 8) | (7u << 5) | 0x3;
}
}  // namespace

esp_err_t Esp32Hal::begin() {
  for (auto& c : cur_) c = NAN;
  gpio_config_t out = {};
  out.pin_bit_mask = (1ULL << board::kNSleep) | (1ULL << board::kStatusLed);
  out.mode = GPIO_MODE_OUTPUT;
  gpio_config(&out);
  gpio_set_level(board::kNSleep, 0);
  gpio_set_level(board::kStatusLed, 0);

  ESP_ERROR_CHECK(initPwm());
  ESP_ERROR_CHECK(initEncoders());
  initI2c();
  ESP_LOGI(TAG, "TCA9555 %s, ADS1115 #1 %s, #2 %s", tca_ok_ ? "ok" : "MISSING", ads_ok_[0] ? "ok" : "missing",
           ads_ok_[1] ? "ok" : "missing");
  return ESP_OK;
}

esp_err_t Esp32Hal::initEncoders() {
  for (int m = 0; m < 6; ++m) {
    pcnt_unit_config_t ucfg = {};
    ucfg.low_limit = -kPcntLimit;
    ucfg.high_limit = kPcntLimit;
    ucfg.flags.accum_count = 1;  // driver extends the 16-bit counter on overflow
    ESP_RETURN_ON_ERROR(pcnt_new_unit(&ucfg, &pcnt_[m]), TAG, "pcnt unit");
    pcnt_glitch_filter_config_t filt = {};
    filt.max_glitch_ns = 1000;
    ESP_RETURN_ON_ERROR(pcnt_unit_set_glitch_filter(pcnt_[m], &filt), TAG, "filter");

    // Full x4 quadrature, as in ESP-IDF's rotary encoder example.
    pcnt_chan_config_t ca = {};
    ca.edge_gpio_num = board::kEncA[m];
    ca.level_gpio_num = board::kEncB[m];
    pcnt_channel_handle_t chan_a = nullptr, chan_b = nullptr;
    ESP_RETURN_ON_ERROR(pcnt_new_channel(pcnt_[m], &ca, &chan_a), TAG, "chan a");
    pcnt_chan_config_t cb = {};
    cb.edge_gpio_num = board::kEncB[m];
    cb.level_gpio_num = board::kEncA[m];
    ESP_RETURN_ON_ERROR(pcnt_new_channel(pcnt_[m], &cb, &chan_b), TAG, "chan b");
    pcnt_channel_set_edge_action(chan_a, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
    pcnt_channel_set_level_action(chan_a, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    pcnt_channel_set_edge_action(chan_b, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
    pcnt_channel_set_level_action(chan_b, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    pcnt_unit_add_watch_point(pcnt_[m], -kPcntLimit);
    pcnt_unit_add_watch_point(pcnt_[m], kPcntLimit);
    ESP_RETURN_ON_ERROR(pcnt_unit_enable(pcnt_[m]), TAG, "enable");
    pcnt_unit_clear_count(pcnt_[m]);
    ESP_RETURN_ON_ERROR(pcnt_unit_start(pcnt_[m]), TAG, "start");
  }
  return ESP_OK;
}

esp_err_t Esp32Hal::initPwm() {
  ledc_timer_config_t t = {};
  t.speed_mode = kLedcMode;
  t.duty_resolution = kLedcBits;
  t.timer_num = LEDC_TIMER_0;
  t.freq_hz = 20000;
  t.clk_cfg = LEDC_AUTO_CLK;
  ESP_RETURN_ON_ERROR(ledc_timer_config(&t), TAG, "ledc timer");
  for (int m = 0; m < 6; ++m) {
    ledc_channel_config_t c = {};
    c.gpio_num = board::kPwm[m];
    c.speed_mode = kLedcMode;
    c.channel = static_cast<ledc_channel_t>(m);
    c.timer_sel = LEDC_TIMER_0;
    c.duty = 0;
    ESP_RETURN_ON_ERROR(ledc_channel_config(&c), TAG, "ledc channel");
  }
  return ESP_OK;
}

void Esp32Hal::initI2c() {
  i2c_master_bus_config_t bc = {};
  bc.i2c_port = I2C_NUM_0;
  bc.sda_io_num = board::kSda;
  bc.scl_io_num = board::kScl;
  bc.clk_source = I2C_CLK_SRC_DEFAULT;
  bc.glitch_ignore_cnt = 7;
  bc.flags.enable_internal_pullup = false;  // 4.7k on the board
  if (i2c_new_master_bus(&bc, &bus_) != ESP_OK) {
    ESP_LOGE(TAG, "I2C bus init failed");
    return;
  }
  auto add = [&](uint8_t addr, i2c_master_dev_handle_t* dev) {
    if (i2c_master_probe(bus_, addr, 50) != ESP_OK) return false;
    i2c_device_config_t dc = {};
    dc.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    dc.device_address = addr;
    dc.scl_speed_hz = 400000;
    return i2c_master_bus_add_device(bus_, &dc, dev) == ESP_OK;
  };
  tca_ok_ = add(board::kTcaAddr, &tca_);
  for (int i = 0; i < 2; ++i) ads_ok_[i] = add(board::kAdsAddr[i], &ads_[i]);
  if (tca_ok_) {
    // P00-P05 outputs (PH), P06/P07 and port 1 inputs; all PH low to start.
    tca_ok_ = tcaWrite(0x02, 0x00) && tcaWrite(0x06, 0xC0) && tcaWrite(0x07, 0xFF);
    ph_applied_ = 0;
  }
}

bool Esp32Hal::tcaWrite(uint8_t reg, uint8_t val) {
  uint8_t buf[2] = {reg, val};
  return i2c_master_transmit(tca_, buf, 2, 10) == ESP_OK;
}

bool Esp32Hal::tcaRead(uint8_t reg, uint8_t* val) {
  return i2c_master_transmit_receive(tca_, &reg, 1, val, 1, 10) == ESP_OK;
}

bool Esp32Hal::adsStart(int chip, int ch) {
  const uint16_t cfg = AdsConfig(ch);
  uint8_t buf[3] = {0x01, static_cast<uint8_t>(cfg >> 8), static_cast<uint8_t>(cfg & 0xFF)};
  return i2c_master_transmit(ads_[chip], buf, 3, 10) == ESP_OK;
}

bool Esp32Hal::adsRead(int chip, float* volts) {
  uint8_t reg = 0x00, rx[2];
  if (i2c_master_transmit_receive(ads_[chip], &reg, 1, rx, 2, 10) != ESP_OK) return false;
  const int16_t raw = static_cast<int16_t>((rx[0] << 8) | rx[1]);
  *volts = raw * (4.096f / 32768.f);
  return true;
}

// I2C housekeeping every 2 ms: apply PH changes, read switches/fault/VM,
// and step the two ADCs round-robin (each channel refreshes every 8 ms).
void Esp32Hal::ioLoop() {
  int ch = 0;
  bool started = false;
  int led_t = 0;
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(2));
    if (tca_ok_) {
      const uint8_t want = ph_want_.load();
      if (want != ph_applied_.load() && tcaWrite(0x02, want)) ph_applied_ = want;
      uint8_t p1;
      if (tcaRead(0x01, &p1)) port1_ = p1;
    }
    if (started) {
      float v;
      if (ads_ok_[0] && adsRead(0, &v)) cur_[ch] = v / board::kCsVoltsPerAmp;
      if (ads_ok_[1] && adsRead(1, &v)) {
        if (ch < 2) cur_[4 + ch] = v / board::kCsVoltsPerAmp;
        if (ch == 2) vm_ = v * board::kVmDivider;
      }
      ch = (ch + 1) % 4;
    }
    started = (ads_ok_[0] && adsStart(0, ch)) | (ads_ok_[1] && adsStart(1, ch));

    // Status LED: 0 off, 1 on, 2 slow blink, 3 fast blink.
    led_t = (led_t + 1) % 500;
    const int p = led_pattern_.load();
    const bool on = p == 1 || (p == 2 && led_t < 250) || (p == 3 && (led_t % 100) < 50);
    gpio_set_level(board::kStatusLed, on);
  }
}

int32_t Esp32Hal::encoder(int m) {
  int v = 0;
  pcnt_unit_get_count(pcnt_[m], &v);
  return v;
}

void Esp32Hal::setMotor(int m, float duty) {
  const uint8_t bit = 1u << m;
  uint8_t want = ph_want_.load();
  want = duty >= 0 ? (want | bit) : (want & ~bit);
  ph_want_ = want;
  // Hold EN low until the expander has actually switched PH for this motor.
  if (((ph_applied_.load() ^ want) & bit) || !tca_ok_) duty = 0;
  const uint32_t d = static_cast<uint32_t>(std::fabs(duty) * kLedcMax + 0.5f);
  ledc_set_duty(kLedcMode, static_cast<ledc_channel_t>(m), d > kLedcMax ? kLedcMax : d);
  ledc_update_duty(kLedcMode, static_cast<ledc_channel_t>(m));
}

void Esp32Hal::enableDrivers(bool on) {
  drivers_on_ = on;
  if (!on)
    for (int m = 0; m < 6; ++m) setMotor(m, 0);
  gpio_set_level(board::kNSleep, on ? 1 : 0);
}

bool Esp32Hal::homeSwitch(int sw) { return tca_ok_ && !(port1_.load() & (1u << (board::kSwitchBit0 + sw))); }
bool Esp32Hal::driverFault() { return tca_ok_ && drivers_on_ && !(port1_.load() & (1u << board::kFaultBit)); }
bool Esp32Hal::motorPowerOk() { return tca_ok_ && (port1_.load() & (1u << board::kVmBit)); }
float Esp32Hal::current(int m) { return cur_[m].load(); }
float Esp32Hal::motorVolts() { return vm_.load(); }
uint32_t Esp32Hal::millis() { return static_cast<uint32_t>(esp_timer_get_time() / 1000); }
