#pragma once

// mjpeg_stream — MJPEG viewer for ESP32-P4 LVGL panels.
//
// Purpose: pull an MJPEG (multipart/x-mixed-replace) stream from a Home
// Assistant camera proxy URL, hardware-decode the JPEG frames with the P4's
// JPEG codec (esp_driver_jpeg), hardware-scale + center-crop them with the
// PPA (esp_driver_ppa) into double-buffered RGB565 PSRAM buffers, and show
// them on an LVGL image widget.
//
// Threading model:
//   - A dedicated FreeRTOS task (created once in setup()) owns the socket,
//     the multipart parser, the JPEG decoder engine and the PPA client. It
//     never touches LVGL. It publishes a finished frame by storing the buffer
//     index into ready_idx_ (an atomic mailbox).
//   - The ESPHome main loop() owns the LVGL widget pointer and the image
//     descriptor. It consumes ready_idx_, flips front_idx_ under swap_mutex_
//     and calls lv_img_set_src(). All public runtime API (start/stop/
//     restart/set_url) must be called from the main loop / YAML lambdas.
//
// Budget note (max concurrent streams): each instance costs ~6.5 MB PSRAM
// worst-case (2x scaled RGB565 buffers + 512 KB JPEG accumulator + a
// source-sized RGB565 decode buffer, ~4.2 MB at 1080p) plus one TCP socket
// and 6-10 Mbps of Wi-Fi/Ethernet bandwidth per 1080p stream. Practical
// ceiling on a 32 MB P4 panel: 2 streams @ 1080p, or 3-4 @ 720p/5fps.
//
// AUDIO note (future): MJPEG carries no audio. Two-way or listen-only audio
// needs an external I2S amp on the speaker FPC plus an RTSP/go2rtc source
// with an audio track — a separate component, not an extension of this one.

#include "esphome/core/defines.h"

#ifdef USE_ESP_IDF

#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/components/lvgl/lvgl_esphome.h"

#include "driver/jpeg_decode.h"
#include "driver/ppa.h"

extern "C" {
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
}

#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

namespace esphome::mjpeg_stream {

enum class StreamState : uint8_t { STOPPED, CONNECTING, LIVE, ERROR_AUTH, ERROR_NET };

class MJPEGStream : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  // Config setters (codegen) -------------------------------------------------
  // Also safe at runtime from the main loop: switching to a different URL
  // un-parks an ERROR_AUTH stream and reconnects a running one.
  void set_url(const std::string &url);
  void set_max_fps(float fps) { this->max_fps_ = fps; }
  void set_max_jpeg_size(uint32_t bytes) { this->max_jpeg_size_ = bytes; }
  void set_max_source_size(uint16_t w, uint16_t h) {
    this->max_source_width_ = w;
    this->max_source_height_ = h;
  }
  void add_target(uint16_t w, uint16_t h) { this->targets_.push_back({w, h}); }
  void set_task_core(uint8_t core) { this->task_core_ = core; }
  void set_task_priority(uint8_t priority) { this->task_priority_ = priority; }
  void set_read_timeout(uint32_t ms) { this->read_timeout_ms_ = ms; }

  // Runtime API — main loop / YAML lambdas ONLY ------------------------------
  // Start (or retarget) the stream: scale into targets[target_idx] and show
  // on img_widget. If already running this only retargets — no reconnect.
  void start(uint8_t target_idx, lv_obj_t *img_widget);
  // Detach the widget and stop; the task closes the socket on its next pass.
  void stop();
  // Force a disconnect + reconnect (e.g. after a network hiccup).
  void restart();
  bool is_live() const { return this->state_.load() == StreamState::LIVE; }
  void add_on_state_callback(std::function<void(StreamState)> &&cb) {
    this->state_callbacks_.add(std::move(cb));
  }

 protected:
  struct Target {
    uint16_t w;
    uint16_t h;
  };

  // ---- Worker task (owns socket + decoder + PPA; never touches LVGL) ------
  static void stream_task_(void *arg);
  void task_main_();
  void park_until_url_change_(uint32_t gen);
  void backoff_wait_();
  int connect_(const std::string &host, uint16_t port);
  void close_socket_();
  bool read_http_headers_(int &status, std::string &content_type, long &content_length);
  bool stream_multipart_(const std::string &delim);
  bool stream_single_jpeg_(long content_length);
  bool skip_to_boundary_(const std::string &delim);
  // 1 = frame in jpeg_buf_, 0 = oversize (dropped), -1 = socket error.
  int accumulate_until_boundary_(const std::string &delim, size_t *out_len);
  void handle_frame_(size_t len);
  void decode_and_scale_(size_t jpeg_len, uint32_t now_ms);

  // Buffered socket reads (4 KB internal chunk, memcpy into PSRAM).
  bool fill_rx_();
  int read_byte_();
  bool read_bytes_(uint8_t *dst, size_t n);  // dst == nullptr drains
  bool read_header_line_(char *out, size_t cap);
  int read_line_raw_(uint8_t *dst, size_t cap, bool *complete);

  // ---- Config ---------------------------------------------------------------
  std::string url_;                       // guarded by url_mutex_
  std::mutex url_mutex_;
  std::atomic<uint32_t> url_gen_{0};      // bumped on every URL change
  float max_fps_{8.0f};
  uint32_t max_jpeg_size_{512 * 1024};
  uint16_t max_source_width_{1920};
  uint16_t max_source_height_{1080};
  std::vector<Target> targets_;
  uint8_t task_core_{1};
  uint8_t task_priority_{4};
  uint32_t read_timeout_ms_{10000};

  // ---- Shared state (main loop <-> task) ------------------------------------
  std::atomic<bool> running_{false};
  std::atomic<bool> reconnect_req_{false};
  std::atomic<bool> auth_parked_{false};
  std::atomic<StreamState> state_{StreamState::STOPPED};
  std::atomic<uint8_t> active_target_{0};

  // Double-buffered scaled RGB565 output. The task scales into the buffer
  // != front_idx_ (chosen under swap_mutex_) and publishes it via ready_idx_;
  // loop() consumes ready_idx_ and flips front_idx_ under the same mutex, so
  // the task never writes the buffer LVGL is currently displaying.
  uint8_t *scaled_buf_[2] = {nullptr, nullptr};
  size_t scaled_buf_size_{0};
  uint16_t buf_w_[2] = {0, 0};
  uint16_t buf_h_[2] = {0, 0};
  std::atomic<int> ready_idx_{-1};  // mailbox: >= 0 means "frame ready"
  int front_idx_{0};                // guarded by swap_mutex_
  std::mutex swap_mutex_;

  // ---- Main-loop-only state --------------------------------------------------
  lv_obj_t *img_widget_{nullptr};  // owned by the main loop; task never reads
  lv_img_dsc_t img_dsc_{};
  StreamState published_state_{StreamState::STOPPED};
  CallbackManager<void(StreamState)> state_callbacks_;
  uint32_t last_stats_ms_{0};
  uint32_t stats_last_ok_{0};
  uint32_t stats_last_net_{0};

  // ---- Task-only state --------------------------------------------------------
  int sock_{-1};
  uint8_t rx_buf_[4096];
  size_t rx_len_{0};
  size_t rx_pos_{0};
  uint8_t *jpeg_buf_{nullptr};  // PSRAM JPEG accumulator (DMA-capable)
  size_t max_jpeg_cap_{0};      // actual allocated (cache-aligned) capacity
  jpeg_decoder_handle_t jpeg_dec_{nullptr};
  uint8_t *dec_buf_{nullptr};   // decoded RGB565, sized lazily to the source
  size_t dec_buf_cap_{0};
  uint32_t dec_dims_{0};        // (w << 16) | h the decode buffer was sized for
  ppa_client_handle_t ppa_{nullptr};
  uint32_t last_present_ms_{0};
  uint32_t backoff_ms_{500};

  // ---- Stats -------------------------------------------------------------------
  std::atomic<uint32_t> frames_ok_{0};       // decoded + scaled + presented
  std::atomic<uint32_t> frames_net_{0};      // frames received off the wire
  std::atomic<uint32_t> frames_dropped_{0};  // oversize / invalid / decode fail
  std::atomic<uint32_t> connects_{0};        // connection attempts
};

class StateTrigger : public Trigger<StreamState> {
 public:
  explicit StateTrigger(MJPEGStream *parent) {
    parent->add_on_state_callback([this](StreamState s) { this->trigger(s); });
  }
};

}  // namespace esphome::mjpeg_stream

#endif  // USE_ESP_IDF
