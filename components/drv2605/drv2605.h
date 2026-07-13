#pragma once
#include "esphome/core/component.h"
#include "esphome/components/i2c/i2c.h"

namespace esphome {
namespace drv2605 {

// Minimal TI DRV2605L haptic driver. Plays the chip's built-in ROM effects
// (1..123) in internal-trigger mode. play() is public so it can be called from
// lambdas (LVGL button on_press, touchscreen on_touch, a template button, etc.).
class DRV2605Component : public Component, public i2c::I2CDevice {
 public:
  void set_lra(bool lra) { this->lra_ = lra; }
  void setup() override;
  void loop() override;  // logs calibration diagnostics once, after the API is up
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  // Trigger one built-in effect (1 = "Strong Click"). Clamped to 1..123.
  void play(uint8_t effect);

  // Touch-feedback strength: 0=off, 1=low, 2=med, 3=high, 4=max.
  void set_level(uint8_t level) { this->level_ = level > 4 ? 4 : level; }
  void set_style(uint8_t style) { this->style_ = style > 2 ? 0 : style; }
  // Play the click for the current strength level (no-op when level 0 / off).
  void click();

 protected:
  bool lra_{true};
  uint8_t level_{3};  // default High
  uint8_t style_{0};  // 0=crisp, 1=soft, 2=double
  uint32_t started_{0};    // millis() at setup, to defer the diag log
  uint32_t last_diag_{0};  // millis() of the last diag log
  uint8_t diag_count_{0};  // repeat the diag a few times so a late log client catches it
};

}  // namespace drv2605
}  // namespace esphome
