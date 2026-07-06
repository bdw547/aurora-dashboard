#include "drv2605.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"  // millis(), delay()

namespace esphome {
namespace drv2605 {

static const char *const TAG = "drv2605";

// DRV2605 register map (subset)
static const uint8_t REG_MODE = 0x01;      // 0x00 = active, internal trigger
static const uint8_t REG_LIBRARY = 0x03;   // effect library select
static const uint8_t REG_WAVESEQ1 = 0x04;  // first effect in the sequence
static const uint8_t REG_WAVESEQ2 = 0x05;  // 0 terminates the sequence
static const uint8_t REG_GO = 0x0C;        // write 1 to fire the sequence
static const uint8_t REG_RATED_VOLTAGE = 0x16;
static const uint8_t REG_OD_CLAMP = 0x17;      // overdrive clamp (max drive voltage)
static const uint8_t REG_FEEDBACK = 0x1A;      // bit7 N_ERM_LRA: 1 = LRA, 0 = ERM
static const uint8_t REG_CONTROL1 = 0x1B;      // bits4:0 DRIVE_TIME (LRA half-period target)
static const uint8_t REG_OL_LRA_PERIOD = 0x20;

// Tuning for the Pimoroni breakout's ELV1411A LRA: resonant freq 150 Hz, 2 Vrms.
// The DRV2605 defaults assume ~205 Hz, so at 150 Hz it drives far off resonance
// (weak/buzzy). DRIVE_TIME = half-period: (1/150/2 - 0.5ms)/0.1ms = 28 -> 0x9C
// (keeps STARTUP_BOOST). OL_LRA_PERIOD = 6.667ms/98.46us = 68 -> 0x44.
// RATED_VOLTAGE ~2 Vrms @150Hz -> 0x55; OD_CLAMP ~3.0 V -> 0x90.
static const uint8_t LRA_RATED = 0x55;
static const uint8_t LRA_ODCLAMP = 0x90;
static const uint8_t LRA_CONTROL1 = 0x9C;
static const uint8_t LRA_OL_PERIOD = 0x44;

void DRV2605Component::setup() {
  // Wake out of standby into internal-trigger mode. This is the first write, so
  // it also tells us whether the chip is actually on the bus.
  if (!this->write_byte(REG_MODE, 0x00)) {
    ESP_LOGE(TAG, "DRV2605 not responding at 0x%02X — check wiring/power", this->address_);
    this->mark_failed();
    return;
  }
  if (this->lra_) {
    uint8_t fb = 0;
    this->read_byte(REG_FEEDBACK, &fb);
    this->write_byte(REG_FEEDBACK, fb | 0x80);  // N_ERM_LRA = 1 (linear actuator)
    // Tell the driver the ELV1411A's real parameters (150 Hz / 2 Vrms) BEFORE
    // calibrating, so it drives on-resonance (strong) and auto-cal locks the
    // right frequency + braking (crisp) instead of the ~205 Hz default (buzzy).
    this->write_byte(REG_RATED_VOLTAGE, LRA_RATED);
    this->write_byte(REG_OD_CLAMP, LRA_ODCLAMP);
    this->write_byte(REG_CONTROL1, LRA_CONTROL1);
    this->write_byte(REG_OL_LRA_PERIOD, LRA_OL_PERIOD);
    // Auto-calibrate braking/back-EMF around the 150 Hz drive time set above.
    // ~1s, blocking (runs once at boot).
    this->write_byte(REG_MODE, 0x07);  // auto-calibration mode
    this->write_byte(REG_GO, 1);
    uint32_t start = millis();
    uint8_t go = 1;
    while ((go & 1) && (millis() - start) < 1500) {
      delay(20);
      this->read_byte(REG_GO, &go);
    }
    this->write_byte(REG_MODE, 0x00);  // back to internal-trigger mode
    this->write_byte(REG_LIBRARY, 6);  // LRA effect library
  } else {
    this->write_byte(REG_LIBRARY, 1);  // ERM effect library A
  }
  // Pre-load a click so a bare GO does something even before play() is called.
  this->write_byte(REG_WAVESEQ1, 1);
  this->write_byte(REG_WAVESEQ2, 0);
}

void DRV2605Component::play(uint8_t effect) {
  if (this->is_failed())
    return;
  if (effect < 1 || effect > 123)
    effect = 1;
  this->write_byte(REG_WAVESEQ1, effect);
  this->write_byte(REG_WAVESEQ2, 0);
  this->write_byte(REG_GO, 1);
}

void DRV2605Component::click() {
  // Strength -> ROM effect. Sharp Clicks (crisp/punchy) 30/60/100%; Max = a
  // sharp double-tick for extra emphasis without being a long buzz.
  static const uint8_t EFFECT[5] = {0, 6, 5, 4, 27};
  uint8_t lvl = this->level_ > 4 ? 4 : this->level_;
  if (EFFECT[lvl] == 0)
    return;  // off
  this->play(EFFECT[lvl]);
}

void DRV2605Component::dump_config() {
  ESP_LOGCONFIG(TAG, "DRV2605L haptic (%s):", this->lra_ ? "LRA" : "ERM");
  LOG_I2C_DEVICE(this);
  if (this->is_failed())
    ESP_LOGE(TAG, "  Communication failed — not found on the I2C bus");
}

}  // namespace drv2605
}  // namespace esphome
