#include "mjpeg_stream.h"

#ifdef USE_ESP_IDF

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"

extern "C" {
#include "lwip/netdb.h"
#include "lwip/sockets.h"
}

#include <strings.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace esphome::mjpeg_stream {

static const char *const TAG = "mjpeg_stream";

static constexpr uint32_t ALIGN_UP_(uint32_t v, uint32_t a) { return (v + a - 1) & ~(a - 1); }

static uint32_t now_ms_() { return (uint32_t) (esp_timer_get_time() / 1000ULL); }

// Case-insensitive substring search (strcasestr is not guaranteed by newlib).
static const char *stristr_(const char *hay, const char *needle) {
  size_t nl = strlen(needle);
  for (const char *h = hay; *h != '\0'; h++) {
    if (strncasecmp(h, needle, nl) == 0)
      return h;
  }
  return nullptr;
}

// http://host[:port]/path — no TLS (rejecting https:// is handled by the caller).
static bool parse_http_url_(const std::string &url, std::string &host, uint16_t &port, std::string &path) {
  if (strncasecmp(url.c_str(), "http://", 7) != 0)
    return false;
  size_t host_start = 7;
  size_t path_start = url.find('/', host_start);
  std::string hostport =
      (path_start == std::string::npos) ? url.substr(host_start) : url.substr(host_start, path_start - host_start);
  path = (path_start == std::string::npos) ? "/" : url.substr(path_start);
  size_t colon = hostport.find(':');
  if (colon != std::string::npos) {
    host = hostport.substr(0, colon);
    long p = strtol(hostport.c_str() + colon + 1, nullptr, 10);
    if (p <= 0 || p > 65535)
      return false;
    port = (uint16_t) p;
  } else {
    host = hostport;
    port = 80;
  }
  return !host.empty();
}

// Redact the query string (HA proxy tokens live there) for logs.
static std::string redact_url_(const std::string &url) {
  size_t q = url.find('?');
  if (q == std::string::npos)
    return url;
  return url.substr(0, q) + "?<redacted>";
}

// boundary may be quoted and may be followed by further parameters.
static bool extract_boundary_(const std::string &content_type, std::string &boundary) {
  const char *p = stristr_(content_type.c_str(), "boundary=");
  if (p == nullptr)
    return false;
  p += 9;
  boundary.clear();
  if (*p == '"') {
    p++;
    while (*p != '\0' && *p != '"')
      boundary += *p++;
  } else {
    while (*p != '\0' && *p != ';' && *p != ' ' && *p != '\t')
      boundary += *p++;
  }
  return !boundary.empty();
}

// ===========================================================================
// Component lifecycle (main loop)
// ===========================================================================

void MJPEGStream::setup() {
  // Size the double buffers for the LARGEST configured target so retargeting
  // never needs a realloc (the hot path stays allocation-free).
  uint32_t max_tw = 0, max_th = 0;
  for (const auto &t : this->targets_) {
    max_tw = std::max(max_tw, (uint32_t) t.w);
    max_th = std::max(max_th, (uint32_t) t.h);
  }
  if (max_tw == 0 || max_th == 0) {
    ESP_LOGE(TAG, "No targets configured");
    this->mark_failed();
    return;
  }

  this->scaled_buf_size_ = ALIGN_UP_(max_tw * max_th * 2, 64);  // PPA wants cache-aligned out size
  for (int i = 0; i < 2; i++) {
    this->scaled_buf_[i] =
        (uint8_t *) heap_caps_aligned_alloc(64, this->scaled_buf_size_, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (this->scaled_buf_[i] == nullptr) {
      ESP_LOGE(TAG, "PSRAM alloc for scaled buffer %d (%u bytes) failed", i, (unsigned) this->scaled_buf_size_);
      this->mark_failed();
      return;
    }
    memset(this->scaled_buf_[i], 0, this->scaled_buf_size_);
  }

  // JPEG accumulator: allocated through the JPEG driver's own allocator so it
  // satisfies the HW decoder's input-bitstream DMA/cache-alignment rules.
  jpeg_decode_memory_alloc_cfg_t in_cfg = {};
  in_cfg.buffer_direction = JPEG_DEC_ALLOC_INPUT_BUFFER;
  size_t got = 0;
  this->jpeg_buf_ = (uint8_t *) jpeg_alloc_decoder_mem(this->max_jpeg_size_, &in_cfg, &got);
  if (this->jpeg_buf_ == nullptr) {
    // Fallback: plain PSRAM (fine on the P4 — the HW JPEG codec DMAs from
    // PSRAM the same way esp_video_camera's encoder path does).
    got = ALIGN_UP_(this->max_jpeg_size_, 64);
    this->jpeg_buf_ = (uint8_t *) heap_caps_aligned_alloc(64, got, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  }
  if (this->jpeg_buf_ == nullptr) {
    ESP_LOGE(TAG, "PSRAM alloc for JPEG accumulator (%u bytes) failed", (unsigned) this->max_jpeg_size_);
    this->mark_failed();
    return;
  }
  this->max_jpeg_cap_ = got;

  // Stills queue: reserve up front so fetch_still() never grows it on the
  // main loop (pushes stay allocation-free apart from the url string copy).
  this->still_queue_.reserve(STILL_QUEUE_CAP);

  // One task for the life of the component; it idles while !running_.
  BaseType_t ok = xTaskCreatePinnedToCore(MJPEGStream::stream_task_, "mjpeg_cam", 12288, this, this->task_priority_,
                                          nullptr, this->task_core_);
  if (ok != pdPASS) {
    ESP_LOGE(TAG, "Failed to create stream task");
    this->mark_failed();
  }
}

void MJPEGStream::loop() {
  // Present the frame the task published — the ONLY place LVGL is touched.
  // Hold swap_mutex_ across the consume+flip so the task can never pick the
  // buffer we are flipping to as its next back buffer (see decode_and_scale_).
  {
    std::lock_guard<std::mutex> lk(this->swap_mutex_);
    int r = this->ready_idx_.exchange(-1);
    if (r >= 0 && this->img_widget_ != nullptr) {
      this->front_idx_ = r;
      this->img_dsc_.header.magic = LV_IMAGE_HEADER_MAGIC;
      this->img_dsc_.header.cf = LV_COLOR_FORMAT_RGB565;
      this->img_dsc_.header.w = this->buf_w_[r];
      this->img_dsc_.header.h = this->buf_h_[r];
      this->img_dsc_.header.stride = (uint32_t) this->buf_w_[r] * 2;
      this->img_dsc_.data = this->scaled_buf_[r];
      this->img_dsc_.data_size = (uint32_t) this->buf_w_[r] * this->buf_h_[r] * 2;
      // No lv_image_cache_drop needed: image caching is compiled out in this
      // build (CONFIG_LV_CACHE_DEF_SIZE=0); the invalidate forces the redraw.
      lv_image_set_src(this->img_widget_, &this->img_dsc_);
      lv_obj_invalidate(this->img_widget_);
    }
  }

  // Apply finished stills (album art) — the other place LVGL is touched.
  // The task filled buf[1 - front] and set pending; consuming it here flips
  // front, points the widget at the fresh half, unhides it (art widgets start
  // hidden over a placeholder src) and clears pending — only then may the task
  // write the now-offscreen half again. No allocation: a fixed 8-slot scan.
  for (auto &s : this->still_slots_) {
    if (!s.pending.load())
      continue;
    s.front = 1 - s.front;  // safe: the task reads front only while !pending
    s.dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
    s.dsc.header.cf = LV_COLOR_FORMAT_RGB565;
    s.dsc.header.w = s.w;
    s.dsc.header.h = s.h;
    s.dsc.header.stride = (uint32_t) s.w * 2;
    s.dsc.data = s.buf[s.front];
    s.dsc.data_size = (uint32_t) s.w * s.h * 2;
    lv_image_set_src(s.widget, &s.dsc);
    lv_obj_remove_flag(s.widget, LV_OBJ_FLAG_HIDDEN);
    lv_obj_invalidate(s.widget);
    s.pending.store(false);
  }

  // Latch state changes onto the main loop so YAML on_state runs here.
  StreamState s = this->state_.load();
  if (s != this->published_state_) {
    this->published_state_ = s;
    this->state_callbacks_.call(s);
  }

  // Periodic stats. Stream stats log while running; stills activity alone
  // (art-only layouts where the stream never starts) also earns a line.
  uint32_t now = millis();
  uint32_t stills_total = this->stills_ok_.load() + this->stills_failed_.load();
  bool stills_active = stills_total != this->stats_last_stills_;
  if ((this->running_.load() || stills_active) && (now - this->last_stats_ms_) >= 10000) {
    if (this->last_stats_ms_ != 0) {
      float dt = (now - this->last_stats_ms_) / 1000.0f;
      uint32_t ok = this->frames_ok_.load();
      uint32_t net = this->frames_net_.load();
      ESP_LOGI(TAG, "stats: %.1f fps shown, %.1f fps net, %u dropped, %u connects, %u stills (%u failed), %u KB PSRAM free",
               (ok - this->stats_last_ok_) / dt, (net - this->stats_last_net_) / dt,
               (unsigned) this->frames_dropped_.load(), (unsigned) this->connects_.load(),
               (unsigned) this->stills_ok_.load(), (unsigned) this->stills_failed_.load(),
               (unsigned) (heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));
      this->stats_last_ok_ = ok;
      this->stats_last_net_ = net;
    } else {
      this->stats_last_ok_ = this->frames_ok_.load();
      this->stats_last_net_ = this->frames_net_.load();
    }
    this->stats_last_stills_ = stills_total;
    this->last_stats_ms_ = now;
  }
}

void MJPEGStream::dump_config() {
  std::string url;
  {
    std::lock_guard<std::mutex> lk(this->url_mutex_);
    url = this->url_;
  }
  ESP_LOGCONFIG(TAG, "MJPEG Stream:");
  ESP_LOGCONFIG(TAG, "  URL: %s", url.empty() ? "(unset)" : redact_url_(url).c_str());
  ESP_LOGCONFIG(TAG, "  Max FPS: %.1f", this->max_fps_);
  ESP_LOGCONFIG(TAG, "  Max JPEG size: %u bytes", (unsigned) this->max_jpeg_size_);
  ESP_LOGCONFIG(TAG, "  Max source: %ux%u", this->max_source_width_, this->max_source_height_);
  for (size_t i = 0; i < this->targets_.size(); i++)
    ESP_LOGCONFIG(TAG, "  Target %u: %ux%u", (unsigned) i, this->targets_[i].w, this->targets_[i].h);
  ESP_LOGCONFIG(TAG, "  Task: core %u, priority %u, read timeout %u ms", this->task_core_, this->task_priority_,
                (unsigned) this->read_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Frames ok/net/dropped: %u/%u/%u, connects: %u", (unsigned) this->frames_ok_.load(),
                (unsigned) this->frames_net_.load(), (unsigned) this->frames_dropped_.load(),
                (unsigned) this->connects_.load());
  ESP_LOGCONFIG(TAG, "  Stills ok/failed: %u/%u", (unsigned) this->stills_ok_.load(),
                (unsigned) this->stills_failed_.load());
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED (buffer allocation)");
}

// ===========================================================================
// Public runtime API (main loop / YAML lambdas only)
// ===========================================================================

void MJPEGStream::set_url(const std::string &url) {
  std::lock_guard<std::mutex> lk(this->url_mutex_);
  if (url == this->url_)
    return;
  this->url_ = url;
  // Bumping the generation un-parks an ERROR_AUTH stream (the park loop
  // watches the counter); a running stream reconnects to the new URL.
  this->url_gen_++;
  if (this->running_.load())
    this->reconnect_req_.store(true);
}

void MJPEGStream::start(uint8_t target_idx, lv_obj_t *img_widget) {
  if (this->targets_.empty() || this->is_failed())
    return;
  if (target_idx >= this->targets_.size())
    target_idx = (uint8_t) (this->targets_.size() - 1);
  this->active_target_.store(target_idx);
  this->img_widget_ = img_widget;  // main-loop-owned; the task never reads it
  // If already running this is just a retarget — the next decoded frame
  // scales to the new size. No reconnect.
  this->running_.store(true);
}

void MJPEGStream::stop() {
  this->img_widget_ = nullptr;
  this->running_.store(false);  // task closes the socket on its next pass
}

void MJPEGStream::restart() { this->reconnect_req_.store(true); }

void MJPEGStream::fetch_still(const std::string &url, lv_obj_t *widget, uint16_t w, uint16_t h) {
  if (this->is_failed() || widget == nullptr || url.empty() || w == 0 || h == 0)
    return;
  std::lock_guard<std::mutex> lk(this->still_mutex_);
  // Latest-wins per widget: replace a queued-but-not-started request in place,
  // so rapid track skips collapse to the newest art instead of stacking.
  for (auto &r : this->still_queue_) {
    if (r.widget == widget) {
      r.url = url;
      r.w = w;
      r.h = h;
      return;
    }
  }
  if (this->still_queue_.size() >= STILL_QUEUE_CAP) {
    ESP_LOGD(TAG, "Still queue full (%u), dropping oldest", (unsigned) STILL_QUEUE_CAP);
    this->still_queue_.erase(this->still_queue_.begin());
  }
  this->still_queue_.push_back(StillReq{url, widget, w, h});
}

// ===========================================================================
// Worker task — owns socket + multipart parser + JPEG decoder + PPA.
// Never touches LVGL.
// ===========================================================================

void MJPEGStream::stream_task_(void *arg) { static_cast<MJPEGStream *>(arg)->task_main_(); }

void MJPEGStream::task_main_() {
  while (true) {
    if (!this->running_.load()) {
      this->close_socket_();
      if (this->state_.load() != StreamState::STOPPED)
        this->state_.store(StreamState::STOPPED);
      // Stills safe point A: stream idle, socket closed, accumulator unused.
      this->service_stills_(true);
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    // Stills safe point B: between connection cycles — every path back to the
    // top of the running branch has closed the socket (or never opened one)
    // and the accumulator holds no un-decoded frame. Covers snapshot mode
    // (which reconnects per frame) and every stream reconnect.
    this->service_stills_(true);

    bool fresh = this->reconnect_req_.exchange(false);  // explicit restart()/set_url()
    std::string url;
    uint32_t gen;
    {
      std::lock_guard<std::mutex> lk(this->url_mutex_);
      url = this->url_;
      gen = this->url_gen_.load();
    }

    if (url.empty()) {
      // No URL yet (HA down / attribute missing): surface an error so the UI
      // can fall back to a snapshot instead of spinning forever.
      this->state_.store(StreamState::ERROR_NET);
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }

    // Stay in ERROR_NET across silent retry cycles so the state doesn't flap
    // ERROR->CONNECTING->ERROR (each flap re-runs the YAML snapshot fallback,
    // which is a blocking fetch). CONNECTING only on fresh start/restart/URL.
    if (fresh || this->state_.load() != StreamState::ERROR_NET)
      this->state_.store(StreamState::CONNECTING);
    if (strncasecmp(url.c_str(), "https://", 8) == 0) {
      // No TLS on this path; park until the URL changes (logs once per URL).
      ESP_LOGE(TAG, "https:// URLs are not supported (no TLS): %s", redact_url_(url).c_str());
      this->state_.store(StreamState::ERROR_AUTH);
      this->park_until_url_change_(gen);
      continue;
    }

    std::string host, path;
    uint16_t port = 80;
    if (!parse_http_url_(url, host, port, path)) {
      ESP_LOGE(TAG, "Invalid URL: %s", redact_url_(url).c_str());
      this->state_.store(StreamState::ERROR_NET);
      this->backoff_wait_();
      continue;
    }

    this->connects_++;
    this->sock_ = this->connect_(host, port);
    if (this->sock_ < 0) {
      ESP_LOGW(TAG, "Connect to %s:%u failed: %s", host.c_str(), port, strerror(errno));
      this->state_.store(StreamState::ERROR_NET);
      this->backoff_wait_();
      continue;
    }

    // HTTP/1.0 on purpose: chunked transfer encoding is illegal in 1.0
    // responses, so aiohttp (HA) and Go net/http (go2rtc) fall back to raw
    // EOF-delimited bodies our multipart parser can read directly.
    std::string req = "GET " + path + " HTTP/1.0\r\nHost: " + host +
                      "\r\nConnection: close\r\nUser-Agent: aurora-mjpeg\r\n\r\n";
    if (send(this->sock_, req.data(), req.size(), 0) != (int) req.size()) {
      ESP_LOGW(TAG, "Request send failed: %s", strerror(errno));
      this->close_socket_();
      this->state_.store(StreamState::ERROR_NET);
      this->backoff_wait_();
      continue;
    }

    int status = 0;
    std::string content_type;
    long content_length = -1;
    if (!this->read_http_headers_(status, content_type, content_length)) {
      ESP_LOGW(TAG, "Failed to read HTTP response headers");
      this->close_socket_();
      this->state_.store(StreamState::ERROR_NET);
      this->backoff_wait_();
      continue;
    }

    // 403 included: Home Assistant answers 403 (not 401) for a bad/expired
    // stream token, and retrying can't help until a fresh URL arrives.
    if (status == 401 || status == 403 || status == 404) {
      ESP_LOGE(TAG, "HTTP %d from %s — check the camera proxy URL/token", status, host.c_str());
      this->close_socket_();
      this->state_.store(StreamState::ERROR_AUTH);
      this->park_until_url_change_(gen);
      continue;
    }
    if (status != 200) {
      ESP_LOGW(TAG, "HTTP %d from %s", status, host.c_str());
      this->close_socket_();
      this->state_.store(StreamState::ERROR_NET);
      this->backoff_wait_();
      continue;
    }

    std::string boundary;
    bool net_ok;
    // Empty-close detection counts frames RECEIVED (net), not presented: a
    // connection that delivered bytes isn't the "server has nothing" case
    // even if every frame was dropped (oversize, decode failure, fps gate).
    uint32_t frames_before = this->frames_net_.load();
    if (stristr_(content_type.c_str(), "multipart") != nullptr && extract_boundary_(content_type, boundary)) {
      // Home Assistant declares "boundary=--frameboundary" — the parameter
      // value already carries the leading dashes the body delimiter uses, so
      // only prepend "--" when the value doesn't start with it (per spec).
      std::string delim = (boundary.rfind("--", 0) == 0) ? boundary : "--" + boundary;
      ESP_LOGI(TAG, "Streaming multipart (boundary '%s')", delim.c_str());
      net_ok = this->stream_multipart_(delim);
      if (!net_ok)
        ESP_LOGW(TAG, "Multipart stream ended without a clean close (timeout or parse failure)");
    } else if (stristr_(content_type.c_str(), "image/jpeg") != nullptr) {
      // Single snapshot: whole body is one frame, then reconnect.
      net_ok = this->stream_single_jpeg_(content_length);
    } else {
      ESP_LOGE(TAG, "Unsupported Content-Type: %s", content_type.c_str());
      net_ok = false;
    }

    this->close_socket_();
    if (!this->running_.load() || this->reconnect_req_.load())
      continue;  // stop()/restart()/set_url() — handled at the top

    bool got_frames = this->frames_ok_.load() != frames_before;
    if (!got_frames) {
      // The server accepted the request but delivered zero frames (e.g. HA's
      // camera proxy 200s then closes when it can't fetch an image from the
      // camera cloud). Every retry makes HA hit the upstream camera API, and
      // cloud cameras (Nest/SDM) rate-limit image requests hard — so escalate
      // to a slow poll instead of hammering: 5s, 10s, ... capped at 120s.
      this->empty_closes_ = std::min<uint32_t>(this->empty_closes_ + 1, 24);
      this->state_.store(StreamState::ERROR_NET);
      uint32_t wait = std::min<uint32_t>(5000u * this->empty_closes_, 120000u);
      ESP_LOGW(TAG, "Stream closed with no frames (%u in a row) — next attempt in %us",
               (unsigned) this->empty_closes_, (unsigned) (wait / 1000));
      uint32_t waited = 0;
      while (this->running_.load() && !this->reconnect_req_.load() && waited < wait) {
        // Stills safe point D: socket closed for the whole wait (up to 120s —
        // don't starve album art while a cloud camera is rate-limiting us).
        this->service_stills_(true);
        vTaskDelay(pdMS_TO_TICKS(100));
        waited += 100;
      }
    } else if (net_ok) {
      this->empty_closes_ = 0;
      // Clean exit (server closed / snapshot mode) — reconnect promptly.
      vTaskDelay(pdMS_TO_TICKS(200));
    } else {
      this->empty_closes_ = 0;
      this->state_.store(StreamState::ERROR_NET);
      this->backoff_wait_();
    }
  }
}

void MJPEGStream::park_until_url_change_(uint32_t gen) {
  this->auth_parked_.store(true);
  this->close_socket_();
  while (this->running_.load() && !this->reconnect_req_.load() && this->url_gen_.load() == gen) {
    // Stills safe point D: parked with the socket closed (possibly for hours).
    this->service_stills_(true);
    vTaskDelay(pdMS_TO_TICKS(250));
  }
  this->auth_parked_.store(false);
}

void MJPEGStream::backoff_wait_() {
  uint32_t waited = 0;
  while (this->running_.load() && !this->reconnect_req_.load() && waited < this->backoff_ms_) {
    // Stills safe point D: every backoff_wait_ call site closed the socket first.
    this->service_stills_(true);
    vTaskDelay(pdMS_TO_TICKS(100));
    waited += 100;
  }
  // 500ms, 1s, 2s, 4s, 8s cap; reset after the first successful frame.
  this->backoff_ms_ = std::min<uint32_t>(this->backoff_ms_ * 2, 8000);
}

// ===========================================================================
// Socket plumbing (task context)
// ===========================================================================

int MJPEGStream::connect_(const std::string &host, uint16_t port) {
  struct addrinfo hints = {};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  struct addrinfo *res = nullptr;
  char portstr[8];
  snprintf(portstr, sizeof(portstr), "%u", port);
  if (getaddrinfo(host.c_str(), portstr, &hints, &res) != 0 || res == nullptr) {
    ESP_LOGW(TAG, "DNS lookup for '%s' failed", host.c_str());
    return -1;
  }
  int fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
  if (fd >= 0) {
    struct timeval tv = {};
    tv.tv_sec = this->read_timeout_ms_ / 1000;
    tv.tv_usec = (this->read_timeout_ms_ % 1000) * 1000;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    if (connect(fd, res->ai_addr, res->ai_addrlen) != 0) {
      ::close(fd);
      fd = -1;
    }
  }
  freeaddrinfo(res);
  return fd;
}

void MJPEGStream::close_socket_() {
  if (this->sock_ >= 0) {
    ::close(this->sock_);
    this->sock_ = -1;
  }
  this->rx_len_ = 0;
  this->rx_pos_ = 0;
}

bool MJPEGStream::fill_rx_() {
  if (this->rx_pos_ < this->rx_len_)
    return true;
  int n = recv(this->sock_, this->rx_buf_, sizeof(this->rx_buf_), 0);
  if (n <= 0)
    return false;  // error, timeout, or peer close
  this->rx_len_ = (size_t) n;
  this->rx_pos_ = 0;
  return true;
}

int MJPEGStream::read_byte_() {
  if (!this->fill_rx_())
    return -1;
  return this->rx_buf_[this->rx_pos_++];
}

bool MJPEGStream::read_bytes_(uint8_t *dst, size_t n) {
  size_t got = 0;
  while (got < n) {
    if (!this->fill_rx_())
      return false;
    size_t avail = this->rx_len_ - this->rx_pos_;
    size_t take = std::min(avail, n - got);
    if (dst != nullptr)
      memcpy(dst + got, this->rx_buf_ + this->rx_pos_, take);
    this->rx_pos_ += take;
    got += take;
  }
  return true;
}

// Text line for HTTP/part headers: strips CR/LF, silently truncates overlong
// lines (still consumes them). Returns false on socket error.
bool MJPEGStream::read_header_line_(char *out, size_t cap) {
  size_t n = 0;
  while (true) {
    int c = this->read_byte_();
    if (c < 0)
      return false;
    if (c == '\n')
      break;
    if (c != '\r' && n < cap - 1)
      out[n++] = (char) c;
  }
  out[n] = '\0';
  return true;
}

// Raw "line": bytes up to and including '\n', or cap bytes if no newline was
// seen. *complete tells the caller whether the next read starts a new line —
// that is how boundary matches are restricted to line starts, so a boundary
// string can never be missed across chunk seams. Returns -1 on socket error.
int MJPEGStream::read_line_raw_(uint8_t *dst, size_t cap, bool *complete) {
  size_t written = 0;
  *complete = false;
  while (written < cap) {
    if (!this->fill_rx_())
      return -1;
    uint8_t *start = this->rx_buf_ + this->rx_pos_;
    size_t avail = this->rx_len_ - this->rx_pos_;
    size_t take = std::min(avail, cap - written);
    auto *nl = (uint8_t *) memchr(start, '\n', take);
    if (nl != nullptr)
      take = (size_t) (nl - start) + 1;
    memcpy(dst + written, start, take);
    written += take;
    this->rx_pos_ += take;
    if (nl != nullptr) {
      *complete = true;
      break;
    }
  }
  return (int) written;
}

bool MJPEGStream::read_http_headers_(int &status, std::string &content_type, long &content_length) {
  char line[512];
  if (!this->read_header_line_(line, sizeof(line)))
    return false;
  const char *sp = strchr(line, ' ');
  status = (sp != nullptr) ? atoi(sp + 1) : 0;
  content_type.clear();
  content_length = -1;
  bool chunked = false;
  size_t total = strlen(line);
  while (true) {
    if (!this->read_header_line_(line, sizeof(line)))
      return false;
    if (line[0] == '\0')
      break;
    total += strlen(line) + 2;
    if (total > 4096) {
      ESP_LOGW(TAG, "HTTP response headers exceed 4KB");
      return false;
    }
    if (strncasecmp(line, "content-type:", 13) == 0) {
      const char *v = line + 13;
      while (*v == ' ' || *v == '\t')
        v++;
      content_type = v;
    } else if (strncasecmp(line, "content-length:", 15) == 0) {
      content_length = strtol(line + 15, nullptr, 10);
    } else if (strncasecmp(line, "transfer-encoding:", 18) == 0 && stristr_(line + 18, "chunked") != nullptr) {
      chunked = true;
    }
  }
  // We request HTTP/1.0 precisely so servers can't chunk the body; a chunked
  // response would interleave framing bytes into the JPEG data and corrupt
  // every frame, so refuse it outright rather than decode garbage.
  if (chunked) {
    ESP_LOGE(TAG, "Server sent Transfer-Encoding: chunked despite HTTP/1.0 request — unsupported");
    return false;
  }
  return true;
}

// ===========================================================================
// Multipart parser (task context)
// ===========================================================================

// A multipart boundary always occupies its own line, so the scan is line
// oriented: only bytes at a line start are candidates. JPEG entropy data
// contains '\n' roughly every 256 bytes, so "lines" stay short.
bool MJPEGStream::skip_to_boundary_(const std::string &delim) {
  uint8_t buf[256];
  bool at_line_start = true;
  while (this->running_.load() && !this->reconnect_req_.load()) {
    bool complete = false;
    int n = this->read_line_raw_(buf, sizeof(buf), &complete);
    if (n < 0)
      return false;
    if (at_line_start && (size_t) n >= delim.size() && memcmp(buf, delim.data(), delim.size()) == 0)
      return true;
    at_line_start = complete;
  }
  return false;
}

int MJPEGStream::accumulate_until_boundary_(const std::string &delim, size_t *out_len) {
  size_t acc = 0;
  bool at_line_start = true;
  bool overflow = false;
  uint8_t chunk[512];
  while (true) {
    bool complete = false;
    int n = this->read_line_raw_(chunk, sizeof(chunk), &complete);
    if (n < 0)
      return -1;
    if (at_line_start && (size_t) n >= delim.size() && memcmp(chunk, delim.data(), delim.size()) == 0)
      break;
    if (!overflow && acc + (size_t) n <= this->max_jpeg_cap_) {
      memcpy(this->jpeg_buf_ + acc, chunk, n);
      acc += (size_t) n;
    } else {
      overflow = true;  // keep draining to the boundary, then drop
    }
    at_line_start = complete;
  }
  if (overflow) {
    ESP_LOGW(TAG, "Frame exceeds max_jpeg_size (%u), dropped", (unsigned) this->max_jpeg_size_);
    return 0;
  }
  // Strip the CRLF that separates the body from the boundary line.
  if (acc >= 2 && this->jpeg_buf_[acc - 1] == '\n' && this->jpeg_buf_[acc - 2] == '\r')
    acc -= 2;
  else if (acc >= 1 && this->jpeg_buf_[acc - 1] == '\n')
    acc -= 1;
  *out_len = acc;
  return 1;
}

bool MJPEGStream::stream_multipart_(const std::string &delim) {
  while (this->running_.load() && !this->reconnect_req_.load()) {
    if (!this->skip_to_boundary_(delim))
      return !this->running_.load() || this->reconnect_req_.load();

    // Part headers (until the blank line).
    char line[512];
    long content_length = -1;
    size_t total = 0;
    bool hdr_ok = true;
    while (true) {
      if (!this->read_header_line_(line, sizeof(line))) {
        hdr_ok = false;
        break;
      }
      if (line[0] == '\0')
        break;
      total += strlen(line) + 2;
      if (total > 4096) {
        hdr_ok = false;
        break;
      }
      if (strncasecmp(line, "content-length:", 15) == 0)
        content_length = strtol(line + 15, nullptr, 10);
    }
    if (!hdr_ok)
      return false;

    size_t jpeg_len = 0;
    if (content_length >= 0) {
      if ((size_t) content_length > this->max_jpeg_cap_) {
        ESP_LOGW(TAG, "Frame of %ld bytes exceeds max_jpeg_size (%u), dropped", content_length,
                 (unsigned) this->max_jpeg_size_);
        this->frames_dropped_++;
        if (!this->read_bytes_(nullptr, (size_t) content_length))  // drain
          return false;
        continue;
      }
      if (!this->read_bytes_(this->jpeg_buf_, (size_t) content_length))
        return false;
      jpeg_len = (size_t) content_length;
    } else {
      int r = this->accumulate_until_boundary_(delim, &jpeg_len);
      if (r < 0)
        return false;
      if (r == 0) {
        this->frames_dropped_++;
        continue;
      }
    }
    this->handle_frame_(jpeg_len);
    // Stills safe point C: the accumulated frame was fully consumed by
    // handle_frame_ (decoded + scaled, or dropped) and the boundary scan for
    // the next frame has not started, so the accumulator is reusable. ONE
    // still per frame bounds the stream stall; process_still_ parks the live
    // stream socket + its buffered rx bytes and restores them afterwards, so
    // the multipart parse resumes byte-exact (TCP flow control simply holds
    // the server's frames back during the fetch).
    this->service_stills_(false);
  }
  return true;
}

bool MJPEGStream::stream_single_jpeg_(long content_length) {
  size_t acc = 0;
  if (content_length >= 0) {
    if ((size_t) content_length > this->max_jpeg_cap_) {
      ESP_LOGW(TAG, "Snapshot of %ld bytes exceeds max_jpeg_size, dropped", content_length);
      this->frames_dropped_++;
      return true;  // reconnect
    }
    if (!this->read_bytes_(this->jpeg_buf_, (size_t) content_length))
      return false;
    acc = (size_t) content_length;
  } else {
    // No Content-Length: read to EOF ("Connection: close" semantics).
    while (true) {
      if (this->rx_pos_ >= this->rx_len_) {
        int n = recv(this->sock_, this->rx_buf_, sizeof(this->rx_buf_), 0);
        if (n == 0)
          break;  // clean EOF — body complete
        if (n < 0)
          return false;
        this->rx_len_ = (size_t) n;
        this->rx_pos_ = 0;
      }
      size_t avail = this->rx_len_ - this->rx_pos_;
      if (acc + avail > this->max_jpeg_cap_) {
        this->frames_dropped_++;
        return true;
      }
      memcpy(this->jpeg_buf_ + acc, this->rx_buf_ + this->rx_pos_, avail);
      acc += avail;
      this->rx_pos_ = this->rx_len_;
    }
  }
  this->handle_frame_(acc);
  // Pace snapshot polling to max_fps before the caller reconnects.
  vTaskDelay(pdMS_TO_TICKS((uint32_t) (1000.0f / this->max_fps_)));
  return true;
}

// ===========================================================================
// Frame gate + HW decode + PPA scale (task context)
// ===========================================================================

void MJPEGStream::handle_frame_(size_t len) {
  this->frames_net_++;
  if (len < 4 || this->jpeg_buf_[0] != 0xFF || this->jpeg_buf_[1] != 0xD8) {
    this->frames_dropped_++;
    return;
  }
  uint32_t now = now_ms_();
  uint32_t interval = (uint32_t) (1000.0f / this->max_fps_);
  // Present gate: rate-limit to max_fps and never overwrite an unconsumed
  // frame (ready_idx_ >= 0 means loop() hasn't picked the last one up yet).
  if (this->ready_idx_.load() >= 0 || (uint32_t) (now - this->last_present_ms_) < interval)
    return;
  this->decode_and_scale_(len, now);
}

// Decode jpeg_buf_[0..jpeg_len) into dec_buf_ with the HW JPEG codec, filling
// *info with the source dimensions. Shared by the stream frame path and the
// stills channel — both run on the worker task, never concurrently, and both
// treat dec_buf_ as scratch consumed by the PPA pass that follows.
bool MJPEGStream::decode_jpeg_(size_t jpeg_len, jpeg_decode_picture_info_t *info) {
  esp_err_t err = jpeg_decoder_get_info(this->jpeg_buf_, (uint32_t) jpeg_len, info);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "jpeg_decoder_get_info failed: %d", (int) err);
    return false;
  }
  if (info->width > this->max_source_width_ || info->height > this->max_source_height_) {
    ESP_LOGW(TAG, "Source %ux%u exceeds max %ux%u, dropped", (unsigned) info->width, (unsigned) info->height,
             this->max_source_width_, this->max_source_height_);
    return false;
  }

  // Lazy init: decoder engine once, decode buffer on first frame / dim change.
  if (this->jpeg_dec_ == nullptr) {
    jpeg_decode_engine_cfg_t eng_cfg = {};
    eng_cfg.intr_priority = 0;
    eng_cfg.timeout_ms = 1000;
    err = jpeg_new_decoder_engine(&eng_cfg, &this->jpeg_dec_);
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "jpeg_new_decoder_engine failed: %d", (int) err);
      this->jpeg_dec_ = nullptr;
      return false;
    }
  }

  // The HW decoder emits whole MCU blocks: the output picture is padded up to
  // 16-pixel alignment, so that is both the buffer size and the PPA stride.
  uint32_t aw = ALIGN_UP_(info->width, 16);
  uint32_t ah = ALIGN_UP_(info->height, 16);
  size_t need = (size_t) aw * ah * 2;  // RGB565
  if (this->dec_buf_ == nullptr || this->dec_buf_cap_ < need) {
    if (this->dec_buf_ != nullptr)
      heap_caps_free(this->dec_buf_);
    jpeg_decode_memory_alloc_cfg_t out_cfg = {};
    out_cfg.buffer_direction = JPEG_DEC_ALLOC_OUTPUT_BUFFER;
    size_t got = 0;
    this->dec_buf_ = (uint8_t *) jpeg_alloc_decoder_mem(need, &out_cfg, &got);
    if (this->dec_buf_ == nullptr) {
      got = ALIGN_UP_(need, 64);
      this->dec_buf_ = (uint8_t *) heap_caps_aligned_alloc(64, got, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (this->dec_buf_ == nullptr) {
      ESP_LOGE(TAG, "Decode buffer alloc (%u bytes) failed", (unsigned) need);
      this->dec_buf_cap_ = 0;
      return false;
    }
    this->dec_buf_cap_ = got;
    ESP_LOGI(TAG, "Decode buffer %u bytes for %ux%u source", (unsigned) got, (unsigned) info->width,
             (unsigned) info->height);
  }
  this->dec_dims_ = (aw << 16) | ah;

  jpeg_decode_cfg_t dec_cfg = {};
  dec_cfg.output_format = JPEG_DECODE_OUT_FORMAT_RGB565;
  dec_cfg.rgb_order = JPEG_DEC_RGB_ELEMENT_ORDER_RGB;
  dec_cfg.conv_std = JPEG_YUV_RGB_CONV_STD_BT601;
  uint32_t out_len = 0;
  err = jpeg_decoder_process(this->jpeg_dec_, &dec_cfg, this->jpeg_buf_, (uint32_t) jpeg_len, this->dec_buf_,
                             (uint32_t) this->dec_buf_cap_, &out_len);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "jpeg_decoder_process failed: %d", (int) err);
    return false;
  }
  return true;
}

// PPA cover-scale + center-crop + byte_swap dec_buf_ (dims from the preceding
// decode_jpeg_, via *info) into `out` (RGB565, tw x th, 64-byte aligned,
// out_size 64-aligned and >= tw*th*2). Shared by streams and stills.
bool MJPEGStream::ppa_scale_into_(const jpeg_decode_picture_info_t &info, uint8_t *out, size_t out_size, uint16_t tw,
                                  uint16_t th) {
  esp_err_t err;
  if (this->ppa_ == nullptr) {
    ppa_client_config_t ppa_cfg = {};
    ppa_cfg.oper_type = PPA_OPERATION_SRM;
    err = ppa_register_client(&ppa_cfg, &this->ppa_);
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "ppa_register_client failed: %d", (int) err);
      this->ppa_ = nullptr;
      return false;
    }
  }

  uint32_t aw = ALIGN_UP_(info.width, 16);
  uint32_t ah = ALIGN_UP_(info.height, 16);

  // Uniform "fill" scale (cover), quantized UP to the PPA's 1/16 steps, then
  // center-crop the input so the output is (up to a floor-rounding pixel)
  // exactly tw x th. Output buffers are zeroed at alloc, so a rounding edge
  // is black.
  float s = std::max((float) tw / info.width, (float) th / info.height);
  int n16 = (int) ceilf(s * 16.0f);
  n16 = std::min(std::max(n16, 1), 256);  // PPA SRM supports 1/16x .. 16x
  float sq = n16 / 16.0f;
  uint32_t bw = std::min<uint32_t>(((uint32_t) tw * 16) / (uint32_t) n16, info.width);
  uint32_t bh = std::min<uint32_t>(((uint32_t) th * 16) / (uint32_t) n16, info.height);
  bw = std::max<uint32_t>(bw, 1);
  bh = std::max<uint32_t>(bh, 1);
  uint32_t ox = (info.width - bw) / 2;
  uint32_t oy = (info.height - bh) / 2;

  ppa_srm_oper_config_t op = {};
  op.in.buffer = this->dec_buf_;
  op.in.pic_w = aw;
  op.in.pic_h = ah;
  op.in.block_w = bw;
  op.in.block_h = bh;
  op.in.block_offset_x = ox;
  op.in.block_offset_y = oy;
  op.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
  op.out.buffer = out;
  op.out.buffer_size = out_size;
  op.out.pic_w = tw;
  op.out.pic_h = th;
  op.out.block_offset_x = 0;
  op.out.block_offset_y = 0;
  op.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
  op.rotation_angle = PPA_SRM_ROTATION_ANGLE_0;
  op.scale_x = sq;
  op.scale_y = sq;
  // The HW JPEG decoder emits RGB565 with the opposite byte order from the
  // panel's little-endian LVGL buffers — without this swap the image renders
  // as rainbow noise. PPA does the swap for free during the scale pass.
  op.byte_swap = true;
  op.mode = PPA_TRANS_MODE_BLOCKING;
  err = ppa_do_scale_rotate_mirror(this->ppa_, &op);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "ppa_do_scale_rotate_mirror failed: %d (src %ux%u crop %ux%u+%u+%u scale %.3f -> %ux%u)", (int) err,
             (unsigned) info.width, (unsigned) info.height, (unsigned) bw, (unsigned) bh, (unsigned) ox, (unsigned) oy,
             sq, (unsigned) tw, (unsigned) th);
    return false;
  }
  return true;
}

void MJPEGStream::decode_and_scale_(size_t jpeg_len, uint32_t now_ms) {
  jpeg_decode_picture_info_t info = {};
  if (!this->decode_jpeg_(jpeg_len, &info)) {
    this->frames_dropped_++;
    return;
  }

  uint8_t tidx = this->active_target_.load();
  if (tidx >= this->targets_.size())
    tidx = (uint8_t) (this->targets_.size() - 1);
  uint16_t tw = this->targets_[tidx].w;
  uint16_t th = this->targets_[tidx].h;

  // Scale into the buffer NOT currently displayed. front_idx_ can only change
  // when loop() consumes ready_idx_, which is -1 until we publish below — so
  // `back` stays valid for the whole PPA operation.
  int back;
  {
    std::lock_guard<std::mutex> lk(this->swap_mutex_);
    back = (this->front_idx_ == 0) ? 1 : 0;
  }

  if (!this->ppa_scale_into_(info, this->scaled_buf_[back], this->scaled_buf_size_, tw, th)) {
    this->frames_dropped_++;
    return;
  }

  this->buf_w_[back] = tw;
  this->buf_h_[back] = th;
  this->ready_idx_.store(back);  // publish to loop()
  this->last_present_ms_ = now_ms;
  this->frames_ok_++;
  this->backoff_ms_ = 500;  // reset after the first successful frame
  if (this->state_.load() != StreamState::LIVE)
    this->state_.store(StreamState::LIVE);
}

// ===========================================================================
// Stills channel (task context) — one-shot JPEG fetches (album art) sharing
// the task's socket plumbing, JPEG accumulator, HW decoder and PPA.
//
// Safe points (the ONLY places service_stills_ is called): the accumulator is
// shared with the stream, so stills must never run between a stream frame's
// accumulate and its decode. All four call sites guarantee jpeg_buf_ holds no
// un-decoded frame:
//   A. task_main_ idle branch (!running_): socket closed, stream inactive.
//   B. task_main_ top of a running connection cycle: every path back there
//      has closed the socket; the previous frame was consumed or dropped.
//   C. stream_multipart_ immediately after handle_frame_(): the frame was
//      decoded+scaled (or dropped) and the next boundary scan hasn't begun.
//      The stream socket stays open — process_still_ parks it (fd + buffered
//      rx bytes) and restores it so the parse resumes byte-exact.
//   D. the wait loops (park_until_url_change_, backoff_wait_, the empty-close
//      poll): socket closed for the whole wait, which can last minutes.
// ===========================================================================

bool MJPEGStream::pop_still_(StillReq &out) {
  std::lock_guard<std::mutex> lk(this->still_mutex_);
  if (this->still_queue_.empty())
    return false;
  out = std::move(this->still_queue_.front());
  this->still_queue_.erase(this->still_queue_.begin());
  return true;
}

void MJPEGStream::service_stills_(bool drain) {
  StillReq req;
  while (this->pop_still_(req)) {
    this->process_still_(req);
    if (!drain)
      break;  // safe point C: one still per stream frame bounds the stall
  }
}

void MJPEGStream::process_still_(const StillReq &req) {
  // Mid-stream service (safe point C): park the live stream socket and its
  // buffered-but-unconsumed rx bytes so the still fetch gets exclusive use of
  // sock_ + rx state (and, by the safe-point contract, jpeg_buf_ + dec_buf_ +
  // decoder + PPA). Restored below; TCP flow control holds the stream's
  // frames back on the server side in the meantime.
  int saved_sock = -1;
  size_t saved_live = 0;
  bool swapped = false;
  if (this->sock_ >= 0) {
    if (this->still_save_buf_ == nullptr) {
      this->still_save_buf_ = (uint8_t *) heap_caps_malloc(sizeof(this->rx_buf_), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
      if (this->still_save_buf_ == nullptr) {
        ESP_LOGW(TAG, "still: save-buffer alloc failed, request dropped");
        this->stills_failed_++;
        return;
      }
    }
    saved_live = this->rx_len_ - this->rx_pos_;
    if (saved_live > 0)
      memcpy(this->still_save_buf_, this->rx_buf_ + this->rx_pos_, saved_live);
    saved_sock = this->sock_;
    this->sock_ = -1;
    this->rx_len_ = 0;
    this->rx_pos_ = 0;
    swapped = true;
  }

  bool ok = this->fetch_and_present_still_(req);
  this->close_socket_();  // closes the STILL socket (if open) + resets rx state

  if (swapped) {
    this->sock_ = saved_sock;
    if (saved_live > 0)
      memcpy(this->rx_buf_, this->still_save_buf_, saved_live);
    this->rx_len_ = saved_live;
    this->rx_pos_ = 0;
  }
  if (ok)
    this->stills_ok_++;
  else
    this->stills_failed_++;  // no retries: art is cosmetic, next track change re-requests
}

bool MJPEGStream::fetch_and_present_still_(const StillReq &req) {
  // Same TLS posture as the stream: plain http only (HA's media proxy is
  // LAN-local http; TLS would cost ~50KB internal RAM per handshake).
  if (strncasecmp(req.url.c_str(), "https://", 8) == 0) {
    ESP_LOGW(TAG, "still: https:// URLs are not supported (no TLS): %s", redact_url_(req.url).c_str());
    return false;
  }
  std::string host, path;
  uint16_t port = 80;
  if (!parse_http_url_(req.url, host, port, path)) {
    ESP_LOGW(TAG, "still: invalid URL: %s", redact_url_(req.url).c_str());
    return false;
  }
  this->sock_ = this->connect_(host, port);
  if (this->sock_ < 0) {
    ESP_LOGW(TAG, "still: connect to %s:%u failed: %s", host.c_str(), port, strerror(errno));
    return false;
  }
  std::string get = "GET " + path + " HTTP/1.0\r\nHost: " + host +
                    "\r\nConnection: close\r\nUser-Agent: aurora-mjpeg\r\n\r\n";
  if (send(this->sock_, get.data(), get.size(), 0) != (int) get.size()) {
    ESP_LOGW(TAG, "still: request send failed: %s", strerror(errno));
    return false;
  }
  int status = 0;
  std::string content_type;
  long content_length = -1;
  if (!this->read_http_headers_(status, content_type, content_length)) {
    ESP_LOGW(TAG, "still: failed to read HTTP response headers");
    return false;
  }
  if (status != 200) {
    ESP_LOGW(TAG, "still: HTTP %d from %s", status, host.c_str());
    return false;
  }
  size_t jpeg_len = 0;
  if (!this->read_still_body_(content_length, &jpeg_len))
    return false;
  if (jpeg_len < 4 || this->jpeg_buf_[0] != 0xFF || this->jpeg_buf_[1] != 0xD8) {
    ESP_LOGW(TAG, "still: body is not a JPEG (%u bytes, Content-Type '%s')", (unsigned) jpeg_len,
             content_type.c_str());
    return false;
  }

  jpeg_decode_picture_info_t info = {};
  if (!this->decode_jpeg_(jpeg_len, &info))
    return false;

  StillSlot *slot = this->claim_still_slot_(req);
  if (slot == nullptr)
    return false;

  // Invariant: the task writes a slot's buffers only while !pending — loop()
  // flips slot->front as it consumes the flag, and the task must never touch
  // the half LVGL displays. A still-pending publish here means loop() hasn't
  // run since the last still for this widget (near-impossible: latest-wins
  // queueing leaves at most one request per widget, and loop() applies within
  // milliseconds while a fetch takes far longer) — wait briefly, then drop.
  for (int i = 0; i < 10 && slot->pending.load(); i++)
    vTaskDelay(pdMS_TO_TICKS(10));
  if (slot->pending.load()) {
    ESP_LOGW(TAG, "still: previous frame for widget %p never applied, dropped", (void *) req.widget);
    return false;
  }
  int back = 1 - slot->front;  // stable: only loop() writes front, and only while pending
  if (!this->ppa_scale_into_(info, slot->buf[back], slot->buf_size, req.w, req.h))
    return false;
  slot->w = req.w;
  slot->h = req.h;
  slot->pending.store(true);  // publish: loop() applies, unhides the widget, clears the flag
  return true;
}

// Read a still body into jpeg_buf_: per Content-Length when given, else to
// EOF (HTTP/1.0 "Connection: close" semantics, like stream_single_jpeg_).
bool MJPEGStream::read_still_body_(long content_length, size_t *out_len) {
  size_t acc = 0;
  if (content_length >= 0) {
    if ((size_t) content_length > this->max_jpeg_cap_) {
      ESP_LOGW(TAG, "still: %ld bytes exceeds max_jpeg_size (%u), dropped", content_length,
               (unsigned) this->max_jpeg_size_);
      return false;
    }
    if (!this->read_bytes_(this->jpeg_buf_, (size_t) content_length))
      return false;
    acc = (size_t) content_length;
  } else {
    while (true) {
      if (this->rx_pos_ >= this->rx_len_) {
        int n = recv(this->sock_, this->rx_buf_, sizeof(this->rx_buf_), 0);
        if (n == 0)
          break;  // clean EOF — body complete
        if (n < 0)
          return false;
        this->rx_len_ = (size_t) n;
        this->rx_pos_ = 0;
      }
      size_t avail = this->rx_len_ - this->rx_pos_;
      if (acc + avail > this->max_jpeg_cap_) {
        ESP_LOGW(TAG, "still: body exceeds max_jpeg_size (%u), dropped", (unsigned) this->max_jpeg_size_);
        return false;
      }
      memcpy(this->jpeg_buf_ + acc, this->rx_buf_ + this->rx_pos_, avail);
      acc += avail;
      this->rx_pos_ = this->rx_len_;
    }
  }
  *out_len = acc;
  return true;
}

// Find (or allocate) the per-widget output slot. Buffers are sized on first
// use for that widget and reused — art card sizes are fixed per widget by the
// generator, so a larger later request signals a codegen bug and is dropped
// (never realloc a buffer LVGL may reference).
MJPEGStream::StillSlot *MJPEGStream::claim_still_slot_(const StillReq &req) {
  size_t need = (size_t) req.w * req.h * 2;
  StillSlot *free_slot = nullptr;
  for (auto &s : this->still_slots_) {
    if (s.widget == req.widget) {
      if (need > s.buf_size) {
        ESP_LOGW(TAG, "still: %ux%u exceeds widget %p buffer (%u B), dropped", req.w, req.h, (void *) req.widget,
                 (unsigned) s.buf_size);
        return nullptr;
      }
      return &s;
    }
    if (s.widget == nullptr && free_slot == nullptr)
      free_slot = &s;
  }
  if (free_slot == nullptr) {
    ESP_LOGW(TAG, "still: widget registry full (%u), request dropped", (unsigned) STILL_SLOT_CAP);
    return nullptr;
  }
  size_t half = ALIGN_UP_((uint32_t) need, 64);  // PPA wants cache-aligned out size
  for (int i = 0; i < 2; i++) {
    free_slot->buf[i] = (uint8_t *) heap_caps_aligned_alloc(64, half, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (free_slot->buf[i] == nullptr) {
      ESP_LOGW(TAG, "still: PSRAM alloc (%u B) for widget %p failed", (unsigned) half, (void *) req.widget);
      if (i == 1) {
        heap_caps_free(free_slot->buf[0]);
        free_slot->buf[0] = nullptr;
      }
      return nullptr;
    }
    memset(free_slot->buf[i], 0, half);
  }
  free_slot->buf_size = half;
  free_slot->front = 0;
  free_slot->widget = req.widget;  // claim last: the slot is complete before it is discoverable
  return free_slot;
}

}  // namespace esphome::mjpeg_stream

#endif  // USE_ESP_IDF
