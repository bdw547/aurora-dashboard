#include "drv2605.h"
#include "esphome/core/log.h"

namespace esphome {
namespace drv2605 {

static const char *const TAG = "drv2605";

// DRV2605 register map (subset)
static const uint8_t REG_MODE = 0x01;      // 0x00 = active, internal trigger
static const uint8_t REG_LIBRARY = 0x03;   // effect library select
static const uint8_t REG_WAVESEQ1 = 0x04;  // first effect in the sequence
static const uint8_t REG_WAVESEQ2 = 0x05;  // 0 terminates the sequence
static const uint8_t REG_GO = 0x0C;        // write 1 to fire the sequence
static const uint8_t REG_FEEDBACK = 0x1A;  // bit7 N_ERM_LRA: 1 = LRA, 0 = ERM

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
    this->write_byte(REG_LIBRARY, 6);           // LRA effect library
  } else {
    this->write_byte(REG_LIBRARY, 1);           // ERM effect library A
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

void DRV2605Component::dump_config() {
  ESP_LOGCONFIG(TAG, "DRV2605L haptic (%s):", this->lra_ ? "LRA" : "ERM");
  LOG_I2C_DEVICE(this);
  if (this->is_failed())
    ESP_LOGE(TAG, "  Communication failed — not found on the I2C bus");
}

}  // namespace drv2605
}  // namespace esphome
