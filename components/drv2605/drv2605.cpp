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
static const uint8_t REG_CONTROL3 = 0x1D;      // bit0 LRA_OPEN_LOOP
static const uint8_t REG_OL_LRA_PERIOD = 0x20;

static const uint8_t REG_STATUS = 0x00;        // bit3 DIAG_RESULT: 1 = auto-cal/diag failed
static const uint8_t REG_CAL_COMP = 0x18;      // auto-cal result: drive-time compensation
static const uint8_t REG_CAL_BEMF = 0x19;      // auto-cal result: back-EMF gain

// LRA drive tuning (~150 Hz / 2 Vrms actuator). A crisp button "click" comes from
// driving the LRA AT resonance and braking the ring-down hard, which is what
// closed-loop + auto-calibration deliver. DRIVE_TIME 0x9C ~= 150 Hz (keeps
// STARTUP_BOOST); OL_LRA_PERIOD 0x44 ~= 150 Hz fallback; RATED ~2 Vrms -> 0x55;
// OD_CLAMP ~3.4 V -> 0xA0 for a harder initial snap (fixes "soft").
static const uint8_t LRA_RATED = 0x55;
static const uint8_t LRA_ODCLAMP = 0xA0;
static const uint8_t LRA_CONTROL1 = 0x9C;
static const uint8_t LRA_OL_PERIOD = 0x44;

// FEEDBACK_CONTROL (0x1A): N_ERM_LRA=1 (LRA) | FB_BRAKE_FACTOR=6x (100) | LOOP_GAIN
// =high (10). Hard braking snaps the ring-down dead (kills the buzz); high loop
// gain locks resonance fast enough inside a short click. Auto-cal fills BEMF_GAIN.
static const uint8_t LRA_FEEDBACK = 0xC8;

void DRV2605Component::setup() {
  // Wake out of standby into internal-trigger mode. This is the first write, so
  // it also tells us whether the chip is actually on the bus.
  if (!this->write_byte(REG_MODE, 0x00)) {
    ESP_LOGE(TAG, "DRV2605 not responding at 0x%02X — check wiring/power", this->address_);
    this->mark_failed();
    return;
  }
  this->started_ = millis();
  if (this->lra_) {
    uint8_t c3 = 0;
    // LRA + hard braking (6x) + high loop gain, all set before auto-cal.
    this->write_byte(REG_FEEDBACK, LRA_FEEDBACK);
    // Give the driver the LRA's rated voltage + a resonance estimate, force
    // CLOSED loop, then auto-calibrate. Closed-loop auto-resonance tracking +
    // calibrated braking is what turns a mushy buzz into a crisp click: it
    // drives AT resonance and actively brakes the ring-down so it stops dead.
    this->write_byte(REG_RATED_VOLTAGE, LRA_RATED);
    this->write_byte(REG_OD_CLAMP, LRA_ODCLAMP);
    this->write_byte(REG_CONTROL1, LRA_CONTROL1);
    this->write_byte(REG_OL_LRA_PERIOD, LRA_OL_PERIOD);
    this->read_byte(REG_CONTROL3, &c3);
    this->write_byte(REG_CONTROL3, c3 & ~0x01);  // LRA_OPEN_LOOP = 0 (closed loop)
    // Auto-calibrate (blocking, ~1s, runs once at boot). Requires the LRA to be
    // connected NOW — calibrating against the wrong actuator gives a bad feel.
    this->write_byte(REG_MODE, 0x07);
    this->write_byte(REG_GO, 1);
    uint32_t start = millis();
    uint8_t go = 1;
    while ((go & 1) && (millis() - start) < 1500) {
      delay(20);
      this->read_byte(REG_GO, &go);
    }
    uint8_t status = 0;
    this->read_byte(REG_STATUS, &status);
    ESP_LOGI(TAG, "LRA auto-calibration %s (status=0x%02X)",
             (status & 0x08) ? "FAILED — check wiring/actuator" : "ok", status);
    this->write_byte(REG_MODE, 0x00);  // back to internal-trigger mode
    this->write_byte(REG_LIBRARY, 6);  // LRA effect library
  } else {
    this->write_byte(REG_LIBRARY, 1);  // ERM effect library A
  }
  // Pre-load a click so a bare GO does something even before play() is called.
  this->write_byte(REG_WAVESEQ1, 1);
  this->write_byte(REG_WAVESEQ2, 0);
}

void DRV2605Component::loop() {
  // Boot-time logs go out over UART, before the network API is up, so they can't
  // be seen with `esphome logs`. Dump the auto-cal results a few times in the
  // first minute (every 10 s) so a log client that attaches late still catches it.
  if (this->is_failed() || this->diag_count_ >= 5)
    return;
  uint32_t now = millis();
  if (now - this->started_ < 8000)
    return;
  if (this->last_diag_ != 0 && now - this->last_diag_ < 10000)
    return;
  this->last_diag_ = now;
  this->diag_count_++;
  uint8_t st = 0, comp = 0, bemf = 0, fb = 0;
  this->read_byte(REG_STATUS, &st);
  this->read_byte(REG_CAL_COMP, &comp);
  this->read_byte(REG_CAL_BEMF, &bemf);
  this->read_byte(REG_FEEDBACK, &fb);
  ESP_LOGI(TAG, "DRV2605 diag: cal=%s status=0x%02X cal_comp=0x%02X cal_bemf=0x%02X feedback=0x%02X",
           (st & 0x08) ? "FAILED" : "ok", st, comp, bemf, fb);
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
  // Each row is a distinct button feel; columns are Off/Low/Med/High/Max.
  // IDs come from TI's LRA ROM library: Sharp Click (4-6), Soft Bump (7-9),
  // and Short Double Click Strong (27-30).
  static const uint8_t EFFECT[3][5] = {
      {0, 6, 5, 4, 1},      // crisp
      {0, 9, 8, 7, 7},      // soft
      {0, 30, 29, 28, 27},  // double
  };
  uint8_t lvl = this->level_ > 4 ? 4 : this->level_;
  uint8_t style = this->style_ > 2 ? 0 : this->style_;
  if (EFFECT[style][lvl] == 0)
    return;  // off
  this->play(EFFECT[style][lvl]);
}

void DRV2605Component::dump_config() {
  ESP_LOGCONFIG(TAG, "DRV2605L haptic (%s):", this->lra_ ? "LRA" : "ERM");
  LOG_I2C_DEVICE(this);
  if (this->is_failed())
    ESP_LOGE(TAG, "  Communication failed — not found on the I2C bus");
}

}  // namespace drv2605
}  // namespace esphome
