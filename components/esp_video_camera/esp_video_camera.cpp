#include "esp_video_camera.h"

#ifdef USE_ESP_IDF

#include "i2c_helper.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include "esphome/components/network/util.h"

#include "esp_heap_caps.h"

#include <cerrno>
#include <cstring>
#include <cstdio>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/time.h>

extern "C" {
#include "esp_video_init.h"
#include "esp_video_device.h"
#include "linux/videodev2.h"
#include "driver/ledc.h"
#include "esp_timer.h"
#include "mbedtls/base64.h"
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#if CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE
#include "esp_intr_alloc.h"
#include "usb/usb_host.h"
#endif
}

// lwip/sockets.h (LWIP_POSIX_SOCKETS_IO_NAMES) remaps the POSIX IO names to its
// socket variants via macros, e.g. close()->lwip_close(), ioctl()->lwip_ioctl().
// That breaks our V4L2 calls — close()/ioctl() on a /dev/videoN fd would be sent
// to lwip and assert/abort (this crashed the panel at boot). Undo the IO-name
// macros so close()/read()/write()/ioctl() go through the VFS and route by fd
// type. Socket-specific names (socket/bind/recv/send/...) stay mapped to lwip_*.
#undef close
#undef read
#undef write
#undef ioctl
#undef fcntl

#ifndef V4L2_CID_JPEG_COMPRESSION_QUALITY
#define V4L2_CID_JPEG_COMPRESSION_QUALITY (V4L2_CID_JPEG_CLASS_BASE + 1)
#endif

namespace esphome::esp_video_camera {

static const char *const TAG = "esp_video_camera";

// --- V4L2 EXT-control helpers ------------------------------------------------
// esp_video implements only the *_EXT_CTRL[S] ioctls (not the legacy
// VIDIOC_{S,G,QUERY}CTRL), so we mirror the ISP pipeline's own calling pattern:
// VIDIOC_QUERY_EXT_CTRL to read ranges, VIDIOC_{G,S}_EXT_CTRLS to read/write.
static bool v4l2_query_ext_(int fd, uint32_t id, struct v4l2_query_ext_ctrl *q) {
  memset(q, 0, sizeof(*q));
  q->id = id;
  return ioctl(fd, VIDIOC_QUERY_EXT_CTRL, q) == 0;
}

static bool v4l2_set_int_(int fd, uint32_t id, int32_t value) {
  struct v4l2_ext_control c;
  struct v4l2_ext_controls cs;
  memset(&c, 0, sizeof(c));
  memset(&cs, 0, sizeof(cs));
  c.id = id;
  c.value = value;
  cs.ctrl_class = V4L2_CID_USER_CLASS;
  cs.count = 1;
  cs.controls = &c;
  return ioctl(fd, VIDIOC_S_EXT_CTRLS, &cs) == 0;
}

// ===========================================================================
// Pipeline init helpers (run esp_video_init on core 0, optional LEDC XCLK)
// ===========================================================================
namespace {

struct VideoInitParams {
  esp_video_init_config_t *config;
  esp_err_t result;
  SemaphoreHandle_t done;
};

// ESP32-P4 camera hardware must be initialised on core 0; run esp_video_init
// there regardless of which core ESPHome runs on.
void video_init_task_core0(void *param) {
  auto *p = static_cast<VideoInitParams *>(param);
  p->result = esp_video_init(p->config);
  xSemaphoreGive(p->done);
  vTaskDelete(nullptr);
}

#if CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE
// Pump USB Host Library events. esp_video is told not to own the USB host lib
// (init_usb_host_lib = false) so that we can tolerate it already being
// installed by another component; when we install it ourselves we run this
// daemon, when it is shared the existing owner pumps the events instead.
void usb_host_lib_daemon_task(void *param) {
  while (true) {
    uint32_t event_flags;
    if (usb_host_lib_handle_events(portMAX_DELAY, &event_flags) == ESP_OK) {
      if (event_flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS)
        usb_host_device_free_all();
    }
  }
}
#endif

// Generate the sensor XCLK with LEDC. For MIPI-CSI sensors esp_video_init() does
// not start XCLK, so non-M5Stack boards must do it before init or the sensor
// stays silent on I2C.
esp_err_t init_xclk_ledc(gpio_num_t gpio_num, uint32_t freq_hz) {
  ledc_timer_config_t timer_conf = {};
  timer_conf.speed_mode = LEDC_LOW_SPEED_MODE;
  timer_conf.timer_num = LEDC_TIMER_0;
  timer_conf.duty_resolution = LEDC_TIMER_1_BIT;
  timer_conf.freq_hz = freq_hz;
  timer_conf.clk_cfg = LEDC_AUTO_CLK;
  esp_err_t ret = ledc_timer_config(&timer_conf);
  if (ret != ESP_OK)
    return ret;

  ledc_channel_config_t ch_conf = {};
  ch_conf.speed_mode = LEDC_LOW_SPEED_MODE;
  ch_conf.channel = LEDC_CHANNEL_0;
  ch_conf.timer_sel = LEDC_TIMER_0;
  ch_conf.intr_type = LEDC_INTR_DISABLE;
  ch_conf.gpio_num = gpio_num;
  ch_conf.duty = 1;  // 50 % duty cycle
  ch_conf.hpoint = 0;
  return ledc_channel_config(&ch_conf);
}

// Parse a resolution string into width/height. Accepts the aliases validated by
// the Python schema or an explicit "WIDTHxHEIGHT". Returns false for "auto".
bool parse_resolution(const std::string &res, uint32_t &width, uint32_t &height) {
  if (res.empty() || res == "auto")
    return false;

  struct ResAlias {
    const char *name;
    uint32_t width;
    uint32_t height;
  };
  static constexpr ResAlias ALIASES[] = {
      {"QVGA", 320, 240}, {"VGA", 640, 480}, {"480P", 640, 480}, {"720P", 1280, 720}, {"1080P", 1920, 1080},
  };
  for (const auto &alias : ALIASES) {
    if (res == alias.name) {
      width = alias.width;
      height = alias.height;
      return true;
    }
  }

  // Parse "WIDTHxHEIGHT" (already validated as digits by the Python schema).
  size_t x_pos = res.find('x');
  if (x_pos == std::string::npos || x_pos == 0 || x_pos + 1 >= res.size())
    return false;
  uint32_t w = 0, h = 0;
  for (size_t i = 0; i < x_pos; i++) {
    if (res[i] < '0' || res[i] > '9')
      return false;
    w = w * 10 + (res[i] - '0');
  }
  for (size_t i = x_pos + 1; i < res.size(); i++) {
    if (res[i] < '0' || res[i] > '9')
      return false;
    h = h * 10 + (res[i] - '0');
  }
  if (w == 0 || h == 0)
    return false;
  width = w;
  height = h;
  return true;
}

}  // namespace

// ===========================================================================
// ESPVideoCameraImage
// ===========================================================================
ESPVideoCameraImage::ESPVideoCameraImage(uint8_t *data, size_t length, uint8_t requesters)
    : data_(data), length_(length), requesters_(requesters) {}

ESPVideoCameraImage::~ESPVideoCameraImage() {
  if (this->data_ != nullptr) {
    heap_caps_free(this->data_);
    this->data_ = nullptr;
  }
}

bool ESPVideoCameraImage::was_requested_by(camera::CameraRequester requester) const {
  return (this->requesters_ & (1 << requester)) != 0;
}

// ===========================================================================
// ESPVideoCameraImageReader
// ===========================================================================
void ESPVideoCameraImageReader::set_image(std::shared_ptr<camera::CameraImage> image) {
  this->image_ = std::move(image);
  this->offset_ = 0;
}

size_t ESPVideoCameraImageReader::available() const {
  if (this->image_ == nullptr)
    return 0;
  return this->image_->get_data_length() - this->offset_;
}

uint8_t *ESPVideoCameraImageReader::peek_data_buffer() {
  if (this->image_ == nullptr)
    return nullptr;
  return this->image_->get_data_buffer() + this->offset_;
}

void ESPVideoCameraImageReader::consume_data(size_t consumed) { this->offset_ += consumed; }

void ESPVideoCameraImageReader::return_image() {
  this->image_.reset();
  this->offset_ = 0;
}

// ===========================================================================
// ESPVideoCamera — setup / pipeline init
// ===========================================================================
void ESPVideoCamera::setup() {
  if (!this->init_pipeline_()) {
    this->mark_failed();
    return;
  }

  // Resolve the device alias to a concrete /dev/videoN path.
  const std::string &d = this->device_;
  this->is_hw_jpeg_ = false;
  if (d.empty() || d == "jpeg" || d == ESP_VIDEO_JPEG_DEVICE_NAME) {
    this->resolved_device_ = ESP_VIDEO_JPEG_DEVICE_NAME;  // /dev/video10
    this->is_hw_jpeg_ = true;
  } else if (d == "csi") {
    this->resolved_device_ = ESP_VIDEO_MIPI_CSI_DEVICE_NAME;  // /dev/video0
  } else if (d.starts_with("uvc")) {
    // "uvc" -> /dev/video40, "uvcN" -> /dev/video4N (N validated as a digit).
    const char *index = (d.size() == 4) ? (d.c_str() + 3) : "0";
    this->resolved_device_ = std::string(ESP_VIDEO_USB_UVC_NAME_PREFIX) + index;
  } else {
    this->resolved_device_ = d;
  }

  int test_fd = open(this->resolved_device_.c_str(), O_RDWR | O_NONBLOCK);
  if (test_fd < 0) {
    ESP_LOGE(TAG, "V4L2 device '%s' unavailable (errno=%d: %s)", this->resolved_device_.c_str(), errno,
             strerror(errno));
    this->mark_failed();
    return;
  }
  close(test_fd);

  ESP_LOGI(TAG, "Camera ready on %s (source: %s)", this->resolved_device_.c_str(), this->device_.c_str());
  // The RTSP server is started lazily from loop() (not here) so the network
  // stack and scheduler are fully up before its tasks spawn.
}

bool ESPVideoCamera::probe_ov02c10_() {
  if (this->i2c_bus_ == nullptr)
    return false;
  const uint8_t kAddr = 0x36;  // OV02C10 SCCB address

  // Software-reset the sensor (reg 0x0103 = 0x01) to clear any partial-wedge
  // state left by a soft reset — this board has no camera reset/pwdn pin, so a
  // reboot/flash can leave the sensor in a state esp_video can't detect (which
  // then asserts and crash-loops). The SW reset mimics a power cycle.
  for (int r = 0; r < 5; r++) {
    uint8_t sw_reset[3] = {0x01, 0x03, 0x01};  // reg 0x0103 = 0x01
    if (this->i2c_bus_->write(kAddr, sw_reset, 3, true) == i2c::ERROR_OK)
      break;
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  vTaskDelay(pdMS_TO_TICKS(30));  // let the reset complete

  for (int attempt = 0; attempt < 25; attempt++) {
    uint8_t reg_h[2] = {0x30, 0x0a};  // OV02C10_REG_SENSOR_ID_H
    uint8_t reg_l[2] = {0x30, 0x0b};  // OV02C10_REG_SENSOR_ID_L
    uint8_t h = 0, l = 0;
    if (this->i2c_bus_->write(kAddr, reg_h, 2, false) == i2c::ERROR_OK &&
        this->i2c_bus_->read(kAddr, &h, 1) == i2c::ERROR_OK &&
        this->i2c_bus_->write(kAddr, reg_l, 2, false) == i2c::ERROR_OK &&
        this->i2c_bus_->read(kAddr, &l, 1) == i2c::ERROR_OK) {
      uint16_t pid = ((uint16_t) h << 8) | l;
      if (pid == 0x5602) {  // OV02C10_PID
        ESP_LOGI(TAG, "OV02C10 confirmed on SCCB (PID 0x%04X, attempt %d)", pid, attempt + 1);
        return true;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(40));
  }
  ESP_LOGW(TAG, "OV02C10 not confirmed on SCCB after retries; skipping camera init "
                "(device boots without camera; power-cycle to recover the sensor)");
  return false;
}

bool ESPVideoCamera::init_pipeline_() {
  if (this->i2c_bus_ == nullptr) {
    ESP_LOGE(TAG, "No I2C bus set");
    return false;
  }
  i2c_master_bus_handle_t i2c_handle = get_i2c_bus_handle(this->i2c_bus_);
  if (i2c_handle == nullptr) {
    ESP_LOGE(TAG, "Could not obtain the ESP-IDF I2C bus handle");
    return false;
  }

  // A "uvc" device streams from a USB camera only. In that case skip the
  // MIPI-CSI pipeline entirely: esp_video_init() runs sensor detection only
  // when config->csi != NULL, so leaving it NULL avoids trying (and failing)
  // to detect a MIPI sensor that isn't present on a USB-only board.
  const bool uvc_only = this->device_.rfind("uvc", 0) == 0;

  // ROBUSTNESS: confirm the OV02C10 over SCCB *before* esp_video_init. The SCCB
  // reads are flaky (shared bus), and esp_video's one-shot detection asserts
  // (NULL semaphore) when it misses — which crash-loops the device and wedges
  // the bus. A retrying ID probe rides out the flakiness; if the sensor still
  // won't identify (truly wedged/absent), we skip camera init so the device
  // boots cleanly instead of crash-looping. A clean power-up then recovers it.
  if (!uvc_only && !this->probe_ov02c10_())
    return false;

  // Start XCLK via LEDC if requested (MIPI sensors need it before init).
  if (!uvc_only && this->enable_xclk_init_ && this->xclk_pin_ != (gpio_num_t) -1) {
    if (init_xclk_ledc(this->xclk_pin_, this->xclk_freq_) != ESP_OK) {
      ESP_LOGE(TAG, "XCLK init failed");
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }

  esp_video_init_csi_config_t csi_config = {};
  csi_config.sccb_config.init_sccb = false;  // reuse the ESPHome I2C bus
  csi_config.sccb_config.i2c_handle = i2c_handle;
  csi_config.sccb_config.freq = 400000;
  csi_config.reset_pin = (gpio_num_t) -1;
  csi_config.pwdn_pin = (gpio_num_t) -1;
  // Note: esp_video >= 2.x no longer takes xclk_pin/xclk_freq in the CSI config.
  // The sensor XCLK is generated separately via LEDC (see init_xclk_ledc above).

  esp_video_init_config_t video_config = {};
  if (!uvc_only)
    video_config.csi = &csi_config;

#if CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE
  esp_video_init_usb_uvc_config_t uvc_config = {};
  if (this->enable_uvc_) {
    uvc_config.uvc.uvc_dev_num = 1;
    uvc_config.uvc.task_stack = 4096;
    uvc_config.uvc.task_priority = 5;
    uvc_config.uvc.task_affinity = -1;

    // The USB Host Library can only be installed once per system. Manage it here
    // instead of letting esp_video own it, so that if another component (e.g.
    // ESPHome's usb_host) has already installed it we share the existing stack
    // instead of aborting esp_video_init(). When we install it ourselves we also
    // run the library event daemon; when it is already installed we leave the
    // events to the existing owner.
    usb_host_config_t host_config = {};
    host_config.skip_phy_setup = false;
    host_config.intr_flags = ESP_INTR_FLAG_LEVEL1;
    esp_err_t host_ret = usb_host_install(&host_config);
    if (host_ret == ESP_OK) {
      xTaskCreatePinnedToCore(usb_host_lib_daemon_task, "usb_lib", 4096, nullptr, 5, nullptr, tskNO_AFFINITY);
    } else if (host_ret == ESP_ERR_INVALID_STATE) {
      ESP_LOGW(TAG, "USB Host already installed by another component; sharing it for UVC");
    } else {
      ESP_LOGE(TAG, "usb_host_install() failed: %s", esp_err_to_name(host_ret));
    }
    uvc_config.usb.init_usb_host_lib = false;  // we manage the USB host library (see above)
    uvc_config.usb.task_stack = 4096;
    uvc_config.usb.task_priority = 5;
    uvc_config.usb.task_affinity = -1;
    video_config.usb_uvc = &uvc_config;
  }
#endif

  // Run esp_video_init() on core 0 (hardware requirement).
  SemaphoreHandle_t done = xSemaphoreCreateBinary();
  if (done == nullptr)
    return false;
  VideoInitParams params = {};
  params.config = &video_config;
  params.done = done;
  TaskHandle_t task = nullptr;
  if (xTaskCreatePinnedToCore(video_init_task_core0, "esp_video_init", 8192, &params, 5, &task, 0) != pdPASS) {
    vSemaphoreDelete(done);
    return false;
  }
  if (xSemaphoreTake(done, pdMS_TO_TICKS(10000)) != pdTRUE) {
    ESP_LOGE(TAG, "esp_video_init() timed out");
    vSemaphoreDelete(done);
    return false;
  }
  vSemaphoreDelete(done);

  if (params.result != ESP_OK) {
    ESP_LOGE(TAG, "esp_video_init() failed: %s", esp_err_to_name(params.result));
    return false;
  }
  this->pipeline_ready_ = true;
  return true;
}

// ===========================================================================
// ESPVideoCamera — streaming / capture
// ===========================================================================
void ESPVideoCamera::loop() {
  // When the RTSP server is enabled it owns the camera from its own task; the
  // ESPHome-loop capture path is disabled so the two never fight over the V4L2
  // device or the encoder. Start the server only once the network is connected:
  // the very first loop() runs before lwIP's TCP/IP core is initialised, and
  // calling socket() then locks a NULL core mutex -> assert -> boot crash-loop
  // (this was the real cause, not the camera). Gating on is_connected() ensures
  // the lwIP stack is fully up before the RTSP task touches a socket.
  if (this->rtsp_port_ != 0) {
    if (!this->rtsp_started_ && network::is_connected()) {
      this->rtsp_started_ = true;
      this->start_rtsp_server_();
    }
    return;
  }
  if (!this->streaming_)
    return;

  if (this->is_hw_jpeg_) {
    this->loop_jpeg_pipeline_();
  } else {
    this->loop_direct_capture_();
  }

  if (this->stream_requesters_ == 0 && this->single_requesters_ == 0)
    this->stop_capture_();
}

void ESPVideoCamera::deliver_frame_(const uint8_t *data, size_t length) {
  if (length == 0)
    return;
  uint32_t now = millis();
  if (this->min_interval_ms_ > 0 && (now - this->last_frame_ms_) < this->min_interval_ms_)
    return;  // throttled to max_framerate
  this->last_frame_ms_ = now;

  uint8_t *copy = (uint8_t *) heap_caps_malloc(length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (copy == nullptr)
    copy = (uint8_t *) heap_caps_malloc(length, MALLOC_CAP_8BIT);
  if (copy == nullptr) {
    ESP_LOGW(TAG, "Failed to allocate %u bytes (frame dropped)", (unsigned) length);
    return;
  }
  memcpy(copy, data, length);
  this->current_image_ =
      std::make_shared<ESPVideoCameraImage>(copy, length, this->single_requesters_ | this->stream_requesters_);
  for (auto *listener : this->listeners_)
    listener->on_camera_image(this->current_image_);
  this->single_requesters_ = 0;
}

void ESPVideoCamera::loop_direct_capture_() {
  // The device already delivers JPEG/MJPEG frames; one MMAP capture queue.
  struct v4l2_buffer buf;
  memset(&buf, 0, sizeof(buf));
  buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buf.memory = V4L2_MEMORY_MMAP;

  if (ioctl(this->capture_fd_, VIDIOC_DQBUF, &buf) < 0) {
    if (errno != EAGAIN)
      ESP_LOGW(TAG, "VIDIOC_DQBUF failed: %s", strerror(errno));
    return;
  }

  if (buf.index < (uint32_t) this->num_capture_buffers_)
    this->deliver_frame_((const uint8_t *) this->capture_buffers_[buf.index].start, buf.bytesused);

  if (ioctl(this->capture_fd_, VIDIOC_QBUF, &buf) < 0)
    ESP_LOGW(TAG, "VIDIOC_QBUF failed: %s", strerror(errno));
}

void ESPVideoCamera::loop_jpeg_pipeline_() {
  // Dequeue one RGB565 frame from the sensor/ISP device (non-blocking).
  struct v4l2_buffer cap_buf;
  memset(&cap_buf, 0, sizeof(cap_buf));
  cap_buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  cap_buf.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->capture_fd_, VIDIOC_DQBUF, &cap_buf) < 0) {
    if (errno != EAGAIN)
      ESP_LOGW(TAG, "capture DQBUF failed: %s", strerror(errno));
    return;
  }

  if (cap_buf.index < (uint32_t) this->num_capture_buffers_ && cap_buf.bytesused > 0 &&
      this->hw_h264_enc_ != nullptr) {
    // H.264 path (Milestone 1 proof): encode the YUV420 frame with the HW
    // H.264 encoder via esp_h264 directly. Copy the frame out of the
    // (re-queueable) V4L2 buffer into our aligned input buffer first.
    size_t copy_len = cap_buf.bytesused;
    if (copy_len > this->h264_in_cap_)
      copy_len = this->h264_in_cap_;
    memcpy(this->h264_in_buf_, this->capture_buffers_[cap_buf.index].start, copy_len);

    esp_h264_enc_in_frame_t in_frame = {};
    in_frame.raw_data.buffer = this->h264_in_buf_;
    in_frame.raw_data.len = copy_len;
    esp_h264_enc_out_frame_t out_frame = {};
    out_frame.raw_data.buffer = this->h264_out_buf_;
    out_frame.raw_data.len = this->h264_out_cap_;

    esp_h264_err_t e = esp_h264_enc_process(this->hw_h264_enc_, &in_frame, &out_frame);
    if (e == ESP_H264_ERR_OK && out_frame.length > 0) {
      static uint32_t fn = 0;
      if ((fn++ % 15) == 0)
        ESP_LOGI(TAG, "H264 frame #%u: %u bytes (in %u)", (unsigned) fn, (unsigned) out_frame.length,
                 (unsigned) copy_len);
      this->deliver_frame_(this->h264_out_buf_, out_frame.length);
    } else {
      ESP_LOGW(TAG, "esp_h264_enc_process failed: %d (out=%u)", (int) e, (unsigned) out_frame.length);
    }
  } else if (cap_buf.index < (uint32_t) this->num_capture_buffers_ && cap_buf.bytesused > 0 &&
             this->hw_jpeg_enc_ != nullptr) {
    // Encode the RGB565 frame with the JPEG hardware via esp_driver_jpeg.
    // jpeg_encoder_process needs DMA-aligned in/out buffers, so copy the frame
    // out of the (re-queueable) V4L2 capture buffer into our aligned input buf.
    size_t copy_len = cap_buf.bytesused;
    if (copy_len > this->enc_in_cap_)
      copy_len = this->enc_in_cap_;
    memcpy(this->enc_in_buf_, this->capture_buffers_[cap_buf.index].start, copy_len);

    jpeg_encode_cfg_t enc_cfg = {};
    enc_cfg.width = this->capture_width_;
    enc_cfg.height = this->capture_height_;
    enc_cfg.src_type = JPEG_ENCODE_IN_FORMAT_RGB565;
    enc_cfg.sub_sample = JPEG_DOWN_SAMPLING_YUV420;
    enc_cfg.image_quality = (this->jpeg_quality_ >= 1 && this->jpeg_quality_ <= 100) ? this->jpeg_quality_ : 80;

    uint32_t out_size = 0;
    esp_err_t err = jpeg_encoder_process(this->hw_jpeg_enc_, &enc_cfg, this->enc_in_buf_, copy_len,
                                         this->enc_out_buf_, this->enc_out_cap_, &out_size);
    if (err == ESP_OK && out_size > 0) {
      ESP_LOGV(TAG, "HW JPEG: %u -> %u bytes", (unsigned) copy_len, (unsigned) out_size);
      this->deliver_frame_(this->enc_out_buf_, out_size);
    } else {
      ESP_LOGW(TAG, "jpeg_encoder_process failed: %s (out=%u)", esp_err_to_name(err), (unsigned) out_size);
    }
  }

  // Return the raw frame to the sensor/ISP device.
  if (ioctl(this->capture_fd_, VIDIOC_QBUF, &cap_buf) < 0)
    ESP_LOGW(TAG, "capture QBUF failed: %s", strerror(errno));
}

camera::CameraImageReader *ESPVideoCamera::create_image_reader() { return new ESPVideoCameraImageReader(); }

void ESPVideoCamera::request_image(camera::CameraRequester requester) {
  this->single_requesters_ |= (1U << requester);
  this->update_capture_state_();
}

void ESPVideoCamera::start_stream(camera::CameraRequester requester) {
  for (auto *listener : this->listeners_)
    listener->on_stream_start();
  this->stream_requesters_ |= (1U << requester);
  this->update_capture_state_();
}

void ESPVideoCamera::stop_stream(camera::CameraRequester requester) {
  for (auto *listener : this->listeners_)
    listener->on_stream_stop();
  this->stream_requesters_ &= ~(1U << requester);
  this->update_capture_state_();
}

void ESPVideoCamera::update_capture_state_() {
  // In RTSP mode the dedicated stream task is the SOLE owner of the V4L2 device.
  // Requests from the ESPHome camera entity (e.g. Home Assistant polling the
  // snapshot entity) must NOT open the device here, or they collide with the
  // stream task -> VIDIOC_REQBUFS "Device or resource busy". The entity is inert.
  if (this->rtsp_port_ != 0)
    return;
  bool wanted = (this->stream_requesters_ != 0) || (this->single_requesters_ != 0);
  if (wanted && !this->streaming_)
    this->start_capture_();
}

bool ESPVideoCamera::configure_capture_format_(uint32_t pixelformat) {
  uint32_t width = 0, height = 0;
  bool force_res = parse_resolution(this->resolution_, width, height);

  struct v4l2_format fmt;
  memset(&fmt, 0, sizeof(fmt));
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  ioctl(this->capture_fd_, VIDIOC_G_FMT, &fmt);  // best-effort starting point
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  fmt.fmt.pix.pixelformat = pixelformat;
  if (force_res) {
    fmt.fmt.pix.width = width;
    fmt.fmt.pix.height = height;
  }
  fmt.fmt.pix.field = V4L2_FIELD_NONE;
  if (ioctl(this->capture_fd_, VIDIOC_S_FMT, &fmt) < 0)
    ESP_LOGW(TAG, "VIDIOC_S_FMT (best-effort resolution) failed: %s", strerror(errno));

  // Read back the resolution actually negotiated by the sensor/ISP.
  memset(&fmt, 0, sizeof(fmt));
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->capture_fd_, VIDIOC_G_FMT, &fmt) == 0) {
    this->capture_width_ = fmt.fmt.pix.width;
    this->capture_height_ = fmt.fmt.pix.height;
  } else {
    this->capture_width_ = width;
    this->capture_height_ = height;
  }
  ESP_LOGI(TAG, "Capture resolution: %ux%u", (unsigned) this->capture_width_, (unsigned) this->capture_height_);
  return true;
}

bool ESPVideoCamera::setup_capture_buffers_() {
  struct v4l2_requestbuffers req;
  memset(&req, 0, sizeof(req));
  req.count = MAX_BUFFERS;
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  req.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->capture_fd_, VIDIOC_REQBUFS, &req) < 0) {
    ESP_LOGE(TAG, "VIDIOC_REQBUFS failed: %s", strerror(errno));
    return false;
  }

  this->num_capture_buffers_ = 0;
  for (unsigned int i = 0; i < req.count && i < MAX_BUFFERS; i++) {
    struct v4l2_buffer buf;
    memset(&buf, 0, sizeof(buf));
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index = i;
    if (ioctl(this->capture_fd_, VIDIOC_QUERYBUF, &buf) < 0) {
      ESP_LOGE(TAG, "VIDIOC_QUERYBUF[%u] failed: %s", i, strerror(errno));
      return false;
    }
    this->capture_buffers_[i].length = buf.length;
    this->capture_buffers_[i].start =
        mmap(nullptr, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, this->capture_fd_, buf.m.offset);
    if (this->capture_buffers_[i].start == MAP_FAILED) {
      this->capture_buffers_[i].start = nullptr;
      ESP_LOGE(TAG, "mmap[%u] failed: %s", i, strerror(errno));
      return false;
    }
    this->num_capture_buffers_++;
    if (ioctl(this->capture_fd_, VIDIOC_QBUF, &buf) < 0) {
      ESP_LOGE(TAG, "VIDIOC_QBUF[%u] failed: %s", i, strerror(errno));
      return false;
    }
  }
  return true;
}

bool ESPVideoCamera::start_capture_() {
  if (this->streaming_)
    return true;
  if (this->is_failed())
    return false;

  bool ok = this->is_hw_jpeg_ ? this->start_jpeg_pipeline_() : this->start_direct_capture_();
  if (!ok) {
    this->stop_capture_();
    return false;
  }
  this->streaming_ = true;
  this->last_frame_ms_ = 0;
  return true;
}

bool ESPVideoCamera::start_direct_capture_() {
  this->capture_fd_ = open(this->resolved_device_.c_str(), O_RDWR | O_NONBLOCK);
  if (this->capture_fd_ < 0) {
    ESP_LOGE(TAG, "open(%s) failed: %s", this->resolved_device_.c_str(), strerror(errno));
    return false;
  }
  if (!this->configure_capture_format_(V4L2_PIX_FMT_MJPEG))
    return false;
  if (!this->setup_capture_buffers_())
    return false;
  int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->capture_fd_, VIDIOC_STREAMON, &type) < 0) {
    ESP_LOGE(TAG, "VIDIOC_STREAMON failed: %s", strerror(errno));
    return false;
  }
  return true;
}

bool ESPVideoCamera::start_jpeg_pipeline_() {
  bool h264 = (this->codec_ == "h264");
  // Stage 1: sensor/ISP (MIPI-CSI) capture device. JPEG path captures RGB565;
  // H.264 path captures YUV420 (the HW H.264 encoder's native input).
  this->capture_fd_ = open(ESP_VIDEO_MIPI_CSI_DEVICE_NAME, O_RDWR | O_NONBLOCK);
  if (this->capture_fd_ < 0) {
    ESP_LOGE(TAG, "open(%s) failed: %s", ESP_VIDEO_MIPI_CSI_DEVICE_NAME, strerror(errno));
    return false;
  }
  if (!this->configure_capture_format_(h264 ? V4L2_PIX_FMT_YUV420 : V4L2_PIX_FMT_RGB565))
    return false;
  if (!this->setup_capture_buffers_())
    return false;
  // Tune the ISP image controls NOW, before STREAMON — while the ISP is
  // quiescent. Writing these controls on /dev/video20 once the capture is
  // streaming races the IPA pipeline (which drives the same ISP every frame for
  // AE/AWB) and wedges the capture. Done here, there are no frames in flight, so
  // it is safe and the values are in place when streaming begins.
  this->apply_image_tuning_();
  int ctype = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->capture_fd_, VIDIOC_STREAMON, &ctype) < 0) {
    ESP_LOGE(TAG, "capture STREAMON failed: %s", strerror(errno));
    return false;
  }

  // Stage 2: direct HW encoder. esp_video 2.2.0's M2M V4L2 encode devices fault
  // the chip ("instruction address misaligned"), so we drive the JPEG / H.264
  // hardware ourselves and encode the frame captured above. Capture (stage 1)
  // and the encoder are independent.
  if (h264) {
    if (!this->ensure_hw_h264_encoder_(this->capture_width_, this->capture_height_))
      return false;
  } else {
    if (!this->ensure_hw_jpeg_encoder_(this->capture_width_, this->capture_height_))
      return false;
  }
  return true;
}

bool ESPVideoCamera::ensure_hw_jpeg_encoder_(uint32_t width, uint32_t height) {
  uint32_t dims = (width << 16) | (height & 0xFFFF);
  if (this->hw_jpeg_enc_ != nullptr && this->enc_dims_ == dims)
    return true;  // already sized for this resolution

  // (Re)create the engine if needed.
  if (this->hw_jpeg_enc_ == nullptr) {
    jpeg_encode_engine_cfg_t eng_cfg = {};
    eng_cfg.timeout_ms = 1000;
    esp_err_t err = jpeg_new_encoder_engine(&eng_cfg, &this->hw_jpeg_enc_);
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "jpeg_new_encoder_engine failed: %s", esp_err_to_name(err));
      return false;
    }
  }

  // (Re)allocate DMA-aligned input (RGB565) + output (JPEG) buffers.
  size_t in_need = (size_t) width * height * 2;   // RGB565 = 2 bytes/px
  size_t out_need = (size_t) width * height;       // JPEG always far smaller
  if (this->enc_in_buf_ == nullptr || this->enc_in_cap_ < in_need) {
    if (this->enc_in_buf_ != nullptr)
      heap_caps_free(this->enc_in_buf_);
    jpeg_encode_memory_alloc_cfg_t mc = {};
    mc.buffer_direction = JPEG_ENC_ALLOC_INPUT_BUFFER;
    this->enc_in_buf_ = (uint8_t *) jpeg_alloc_encoder_mem(in_need, &mc, &this->enc_in_cap_);
    if (this->enc_in_buf_ == nullptr) {
      ESP_LOGE(TAG, "JPEG encoder input alloc (%u) failed", (unsigned) in_need);
      return false;
    }
  }
  if (this->enc_out_buf_ == nullptr || this->enc_out_cap_ < out_need) {
    if (this->enc_out_buf_ != nullptr)
      heap_caps_free(this->enc_out_buf_);
    jpeg_encode_memory_alloc_cfg_t mc = {};
    mc.buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER;
    this->enc_out_buf_ = (uint8_t *) jpeg_alloc_encoder_mem(out_need, &mc, &this->enc_out_cap_);
    if (this->enc_out_buf_ == nullptr) {
      ESP_LOGE(TAG, "JPEG encoder output alloc (%u) failed", (unsigned) out_need);
      return false;
    }
  }
  this->enc_dims_ = dims;
  return true;
}

bool ESPVideoCamera::ensure_hw_h264_encoder_(uint32_t width, uint32_t height) {
  uint32_t dims = (width << 16) | (height & 0xFFFF);
  if (this->hw_h264_enc_ != nullptr && this->h264_dims_ == dims)
    return true;

  if (this->hw_h264_enc_ == nullptr) {
    int fps = (int) this->max_framerate_;
    if (fps < 1)
      fps = 1;
    esp_h264_enc_cfg_hw_t cfg = {};
    cfg.pic_type = ESP_H264_RAW_FMT_O_UYY_E_VYY;  // YUV420 from the ISP
    cfg.gop = (uint8_t) fps;                       // ~1 keyframe / second
    cfg.fps = (uint8_t) fps;
    cfg.res.width = width;
    cfg.res.height = height;
    // Higher bitrate + a tighter QP ceiling so noisy/bright scenes (white
    // surfaces, where sensor grain + the contrast boost add high-frequency
    // detail) don't get crushed into blocky macroblocks by the rate control.
    cfg.rc.bitrate = (width >= 1280) ? 4000000 : 2000000;
    cfg.rc.qp_min = 20;
    cfg.rc.qp_max = 40;
    esp_h264_err_t e = esp_h264_enc_hw_new(&cfg, &this->hw_h264_enc_);
    if (e != ESP_H264_ERR_OK) {
      ESP_LOGE(TAG, "esp_h264_enc_hw_new failed: %d (res %ux%u)", (int) e, (unsigned) width, (unsigned) height);
      this->hw_h264_enc_ = nullptr;
      return false;
    }
    e = esp_h264_enc_open(this->hw_h264_enc_);
    if (e != ESP_H264_ERR_OK) {
      ESP_LOGE(TAG, "esp_h264_enc_open failed: %d", (int) e);
      esp_h264_enc_del(this->hw_h264_enc_);
      this->hw_h264_enc_ = nullptr;
      return false;
    }
  }

  size_t in_need = (size_t) width * height * 3 / 2;  // YUV420
  size_t out_need = (size_t) width * height;          // generous for an H.264 NAL
  if (this->h264_in_buf_ == nullptr || this->h264_in_cap_ < in_need) {
    if (this->h264_in_buf_ != nullptr)
      heap_caps_free(this->h264_in_buf_);
    this->h264_in_buf_ = (uint8_t *) heap_caps_aligned_alloc(128, in_need, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (this->h264_in_buf_ == nullptr) {
      ESP_LOGE(TAG, "h264 input alloc (%u) failed", (unsigned) in_need);
      return false;
    }
    this->h264_in_cap_ = in_need;
  }
  if (this->h264_out_buf_ == nullptr || this->h264_out_cap_ < out_need) {
    if (this->h264_out_buf_ != nullptr)
      heap_caps_free(this->h264_out_buf_);
    this->h264_out_buf_ = (uint8_t *) heap_caps_aligned_alloc(128, out_need, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (this->h264_out_buf_ == nullptr) {
      ESP_LOGE(TAG, "h264 output alloc (%u) failed", (unsigned) out_need);
      return false;
    }
    this->h264_out_cap_ = out_need;
  }
  this->h264_dims_ = dims;
  ESP_LOGI(TAG, "H.264 HW encoder ready %ux%u", (unsigned) width, (unsigned) height);
  return true;
}

void ESPVideoCamera::stop_capture_() {
  if (this->jpeg_fd_ >= 0) {
    int otype = V4L2_BUF_TYPE_VIDEO_OUTPUT;
    int jtype = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(this->jpeg_fd_, VIDIOC_STREAMOFF, &otype);
    ioctl(this->jpeg_fd_, VIDIOC_STREAMOFF, &jtype);
    if (this->jpeg_out_buffer_.start != nullptr) {
      munmap(this->jpeg_out_buffer_.start, this->jpeg_out_buffer_.length);
      this->jpeg_out_buffer_.start = nullptr;
    }
    close(this->jpeg_fd_);
    this->jpeg_fd_ = -1;
  }
  if (this->capture_fd_ >= 0) {
    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(this->capture_fd_, VIDIOC_STREAMOFF, &type);
    for (int i = 0; i < this->num_capture_buffers_; i++) {
      if (this->capture_buffers_[i].start != nullptr) {
        munmap(this->capture_buffers_[i].start, this->capture_buffers_[i].length);
        this->capture_buffers_[i].start = nullptr;
      }
    }
    close(this->capture_fd_);
    this->capture_fd_ = -1;
  }
  this->num_capture_buffers_ = 0;
  this->streaming_ = false;
}

void ESPVideoCamera::dump_config() {
  ESP_LOGCONFIG(TAG, "ESP-Video Camera:");
  ESP_LOGCONFIG(TAG, "  Name: %s", this->get_name().c_str());
  ESP_LOGCONFIG(TAG, "  Source: %s (%s)", this->device_.c_str(), this->resolved_device_.c_str());
  ESP_LOGCONFIG(TAG, "  Resolution: %s", this->resolution_.c_str());
  if (this->is_hw_jpeg_)
    ESP_LOGCONFIG(TAG, "  JPEG quality: %d", this->jpeg_quality_);
  ESP_LOGCONFIG(TAG, "  Max framerate: %.1f fps", this->max_framerate_);
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED");
  if (this->rtsp_port_ != 0)
    ESP_LOGCONFIG(TAG, "  RTSP: rtsp://<ip>:%u/cam (codec: %s)", this->rtsp_port_, this->codec_.c_str());
}

// ===========================================================================
// ESPVideoCamera — RTSP / RTP(H.264) server (Milestone 2)
// ===========================================================================

// Index of the next 3-byte Annex-B start code (00 00 01) at/after `from`.
static size_t next_start_code_(const uint8_t *b, size_t len, size_t from) {
  for (size_t k = from; k + 3 <= len; k++)
    if (b[k] == 0 && b[k + 1] == 0 && b[k + 2] == 1)
      return k;
  return len;
}

// Capture one YUV420 frame and HW-encode it to an H.264 Annex-B access unit.
// Returns the encoder's output buffer (valid until the next call). False on
// EAGAIN (no frame ready) or encode error.
bool ESPVideoCamera::capture_h264_(const uint8_t **nal, size_t *len) {
  if (this->hw_h264_enc_ == nullptr || this->capture_fd_ < 0)
    return false;
  struct v4l2_buffer cap_buf;
  memset(&cap_buf, 0, sizeof(cap_buf));
  cap_buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  cap_buf.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->capture_fd_, VIDIOC_DQBUF, &cap_buf) < 0)
    return false;  // EAGAIN

  bool ok = false;
  if (cap_buf.index < (uint32_t) this->num_capture_buffers_ && cap_buf.bytesused > 0) {
    size_t copy_len = cap_buf.bytesused;
    if (copy_len > this->h264_in_cap_)
      copy_len = this->h264_in_cap_;
    memcpy(this->h264_in_buf_, this->capture_buffers_[cap_buf.index].start, copy_len);

    esp_h264_enc_in_frame_t in_frame = {};
    in_frame.raw_data.buffer = this->h264_in_buf_;
    in_frame.raw_data.len = copy_len;
    esp_h264_enc_out_frame_t out_frame = {};
    out_frame.raw_data.buffer = this->h264_out_buf_;
    out_frame.raw_data.len = this->h264_out_cap_;
    esp_h264_err_t e = esp_h264_enc_process(this->hw_h264_enc_, &in_frame, &out_frame);
    if (e == ESP_H264_ERR_OK && out_frame.length > 0) {
      *nal = this->h264_out_buf_;
      *len = out_frame.length;
      ok = true;
    } else {
      ESP_LOGW(TAG, "esp_h264_enc_process failed: %d", (int) e);
    }
  }
  if (ioctl(this->capture_fd_, VIDIOC_QBUF, &cap_buf) < 0)
    ESP_LOGW(TAG, "capture QBUF failed: %s", strerror(errno));
  return ok;
}

// Capture frames until we have both SPS (type 7) and PPS (type 8) for the SDP.
// Pull SPS (NAL type 7) and PPS (type 8) out of an Annex-B access unit and
// cache them for the SDP. Called by the stream task (the sole camera owner).
void ESPVideoCamera::extract_params_(const uint8_t *au, size_t au_len) {
  if (this->params_ready_)
    return;
  size_t pos = next_start_code_(au, au_len, 0);
  while (pos < au_len) {
    size_t s = pos + 3;
    size_t nxt = next_start_code_(au, au_len, s);
    size_t e = nxt;
    if (e > s && e < au_len && au[e - 1] == 0)
      e--;  // 4-byte start code: drop the trailing leading-zero
    if (e > s) {
      uint8_t type = au[s] & 0x1F;
      if (type == 7 && (e - s) <= sizeof(this->sps_)) {
        memcpy(this->sps_, au + s, e - s);
        this->sps_len_ = e - s;
      } else if (type == 8 && (e - s) <= sizeof(this->pps_)) {
        memcpy(this->pps_, au + s, e - s);
        this->pps_len_ = e - s;
      }
    }
    pos = nxt;
  }
  if (this->sps_len_ > 0 && this->pps_len_ > 0) {
    this->params_ready_ = true;
    ESP_LOGI(TAG, "RTSP: SPS/PPS captured (%u/%u bytes)", (unsigned) this->sps_len_, (unsigned) this->pps_len_);
  }
}

// DESCRIBE handler waits for the stream task (sole camera owner) to publish
// SPS/PPS — it must NOT open the camera itself (that races the stream task and
// causes VIDIOC_REQBUFS EBUSY).
bool ESPVideoCamera::ensure_params_() {
  for (int i = 0; i < 200 && !this->params_ready_; i++)
    vTaskDelay(pdMS_TO_TICKS(20));
  if (!this->params_ready_)
    ESP_LOGW(TAG, "RTSP: SPS/PPS not ready for SDP");
  return this->params_ready_;
}

void ESPVideoCamera::build_sdp_(char *out, size_t out_size, uint32_t server_ip) {
  unsigned char b64sps[160], b64pps[64];
  size_t o1 = 0, o2 = 0;
  mbedtls_base64_encode(b64sps, sizeof(b64sps), &o1, this->sps_, this->sps_len_);
  b64sps[o1] = 0;
  mbedtls_base64_encode(b64pps, sizeof(b64pps), &o2, this->pps_, this->pps_len_);
  b64pps[o2] = 0;
  char plid[8] = "42001f";
  if (this->sps_len_ >= 4)
    snprintf(plid, sizeof(plid), "%02x%02x%02x", this->sps_[1], this->sps_[2], this->sps_[3]);
  snprintf(out, out_size,
           "v=0\r\n"
           "o=- 0 0 IN IP4 %u.%u.%u.%u\r\n"
           "s=Aurora Camera\r\n"
           "c=IN IP4 0.0.0.0\r\n"
           "t=0 0\r\n"
           "m=video 0 RTP/AVP 96\r\n"
           "a=rtpmap:96 H264/90000\r\n"
           "a=fmtp:96 packetization-mode=1;profile-level-id=%s;sprop-parameter-sets=%s,%s\r\n"
           "a=control:trackID=0\r\n",
           (unsigned) (server_ip >> 24) & 0xFF, (unsigned) (server_ip >> 16) & 0xFF,
           (unsigned) (server_ip >> 8) & 0xFF, (unsigned) server_ip & 0xFF, plid, b64sps, b64pps);
}

// Send one NAL (start-code stripped, including its 1-byte header) as RTP,
// fragmenting with FU-A when larger than the payload limit.
// Send one RTP packet over UDP (sendto) or, when the client negotiated
// RTP/AVP/TCP, interleaved on the RTSP control connection (RFC 2326: '$',
// channel, 16-bit big-endian length, then the packet).
void ESPVideoCamera::send_rtp_packet_(const uint8_t *pkt, size_t len) {
  if (this->rtp_over_tcp_) {
    if (this->rtsp_client_fd_ < 0)
      return;
    uint8_t hdr[4] = {'$', this->rtp_tcp_channel_, (uint8_t) (len >> 8), (uint8_t) (len & 0xFF)};
    std::lock_guard<std::mutex> lk(this->tcp_send_mutex_);
    send(this->rtsp_client_fd_, hdr, 4, 0);
    send(this->rtsp_client_fd_, pkt, len, 0);
  } else {
    if (this->rtp_fd_ < 0)
      return;
    struct sockaddr_in dst;
    memset(&dst, 0, sizeof(dst));
    dst.sin_family = AF_INET;
    dst.sin_addr.s_addr = this->rtp_client_ip_;
    dst.sin_port = htons(this->rtp_client_port_);
    sendto(this->rtp_fd_, pkt, len, 0, (struct sockaddr *) &dst, sizeof(dst));
  }
}

// Mutex-guarded send on the control socket (RTSP responses), so they can't
// interleave with RTP packets when the stream runs over TCP.
void ESPVideoCamera::locked_send_(const void *buf, size_t len) {
  if (this->rtsp_client_fd_ < 0)
    return;
  std::lock_guard<std::mutex> lk(this->tcp_send_mutex_);
  send(this->rtsp_client_fd_, buf, len, 0);
}

void ESPVideoCamera::rtp_send_nal_(const uint8_t *nal, size_t len, uint32_t ts, bool marker) {
  if (len == 0)
    return;
  static const size_t MAX_PAYLOAD = 1400;
  uint8_t pkt[1460];
  if (len <= MAX_PAYLOAD) {
    pkt[0] = 0x80;
    pkt[1] = (uint8_t) (96 | (marker ? 0x80 : 0));
    pkt[2] = this->rtp_seq_ >> 8;
    pkt[3] = this->rtp_seq_ & 0xFF;
    pkt[4] = ts >> 24;
    pkt[5] = ts >> 16;
    pkt[6] = ts >> 8;
    pkt[7] = ts;
    pkt[8] = this->rtp_ssrc_ >> 24;
    pkt[9] = this->rtp_ssrc_ >> 16;
    pkt[10] = this->rtp_ssrc_ >> 8;
    pkt[11] = this->rtp_ssrc_;
    memcpy(pkt + 12, nal, len);
    this->send_rtp_packet_(pkt, 12 + len);
    this->rtp_seq_++;
    return;
  }
  // FU-A
  uint8_t nri = nal[0] & 0x60;
  uint8_t type = nal[0] & 0x1F;
  const uint8_t *p = nal + 1;
  size_t rem = len - 1;
  bool first = true;
  while (rem > 0) {
    size_t frag = rem > (MAX_PAYLOAD - 2) ? (MAX_PAYLOAD - 2) : rem;
    bool last = (frag == rem);
    pkt[0] = 0x80;
    pkt[1] = (uint8_t) (96 | ((marker && last) ? 0x80 : 0));
    pkt[2] = this->rtp_seq_ >> 8;
    pkt[3] = this->rtp_seq_ & 0xFF;
    pkt[4] = ts >> 24;
    pkt[5] = ts >> 16;
    pkt[6] = ts >> 8;
    pkt[7] = ts;
    pkt[8] = this->rtp_ssrc_ >> 24;
    pkt[9] = this->rtp_ssrc_ >> 16;
    pkt[10] = this->rtp_ssrc_ >> 8;
    pkt[11] = this->rtp_ssrc_;
    pkt[12] = (uint8_t) (nri | 28);                                       // FU indicator: F=0, NRI, type=28
    pkt[13] = (uint8_t) ((first ? 0x80 : 0) | (last ? 0x40 : 0) | type);  // FU header
    memcpy(pkt + 14, p, frag);
    this->send_rtp_packet_(pkt, 14 + frag);
    this->rtp_seq_++;
    p += frag;
    rem -= frag;
    first = false;
  }
}

void ESPVideoCamera::rtp_send_access_unit_(const uint8_t *au, size_t au_len, uint32_t ts) {
  // Collect NAL boundaries so the marker bit lands on the final NAL.
  struct {
    const uint8_t *p;
    size_t n;
  } nals[48];
  int cnt = 0;
  size_t pos = next_start_code_(au, au_len, 0);
  while (pos < au_len && cnt < 48) {
    size_t s = pos + 3;
    size_t nxt = next_start_code_(au, au_len, s);
    size_t e = nxt;
    if (e > s && e < au_len && au[e - 1] == 0)
      e--;
    if (e > s) {
      nals[cnt].p = au + s;
      nals[cnt].n = e - s;
      cnt++;
    }
    pos = nxt;
  }
  for (int k = 0; k < cnt; k++)
    this->rtp_send_nal_(nals[k].p, nals[k].n, ts, k == cnt - 1);
}

void ESPVideoCamera::rtsp_stream_task_(void *arg) {
  auto *self = (ESPVideoCamera *) arg;
  // Open the camera ONCE and keep it open for the task's lifetime. This task is
  // the SOLE owner of the V4L2 capture + H.264 encoder; opening/closing per
  // client (or from two tasks) causes VIDIOC_REQBUFS "Device or resource busy".
  while (!self->start_jpeg_pipeline_()) {
    ESP_LOGW(TAG, "RTSP: camera capture not ready yet, retrying...");
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
  if (self->rtp_fd_ < 0)
    self->rtp_fd_ = socket(AF_INET, SOCK_DGRAM, 0);
  ESP_LOGI(TAG, "RTSP: capture pipeline open; stream task ready");

  while (true) {
    const uint8_t *nal;
    size_t len;
    if (self->capture_h264_(&nal, &len)) {
      // Cache SPS/PPS from the very first frames so DESCRIBE can build the SDP.
      if (!self->params_ready_)
        self->extract_params_(nal, len);
      // Only emit RTP while a client is playing; otherwise just drain the
      // encoder (keeps the pipeline warm without churning the V4L2 device).
      if (self->rtsp_playing_) {
        uint32_t ts = (uint32_t) ((esp_timer_get_time() * 9) / 100);  // us -> 90 kHz
        self->rtp_send_access_unit_(nal, len, ts);
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(5));
    }
  }
}

// Set one ISP integer control to a target derived from its queried range. frac
// picks a point in [min,max]; when use_default is set the control is left at its
// neutral default instead (used for hue, whose neutral point is the default, not
// the range midpoint).
void ESPVideoCamera::tune_ctrl_(int fd, uint32_t id, float frac, bool use_default, const char *name) {
  struct v4l2_query_ext_ctrl q;
  if (!v4l2_query_ext_(fd, id, &q)) {
    ESP_LOGW(TAG, "image tuning: '%s' not supported by ISP", name);
    return;
  }
  if (q.type != V4L2_CTRL_TYPE_INTEGER && q.type != V4L2_CTRL_TYPE_INTEGER64 &&
      q.type != V4L2_CTRL_TYPE_BOOLEAN && q.type != V4L2_CTRL_TYPE_MENU) {
    ESP_LOGW(TAG, "image tuning: '%s' is not a scalar control (type=%u) — skipping", name,
             (unsigned) q.type);
    return;
  }
  int32_t target;
  if (use_default) {
    target = (int32_t) q.default_value;
  } else {
    double v = (double) q.minimum + frac * ((double) q.maximum - (double) q.minimum);
    target = (int32_t) (v + (v >= 0 ? 0.5 : -0.5));
    if (q.step > 1) {  // snap to the control's step grid
      int64_t steps = (target - (int64_t) q.minimum) / (int64_t) q.step;
      target = (int32_t) ((int64_t) q.minimum + steps * (int64_t) q.step);
    }
  }
  if (target < (int32_t) q.minimum)
    target = (int32_t) q.minimum;
  if (target > (int32_t) q.maximum)
    target = (int32_t) q.maximum;

  ESP_LOGI(TAG, "image tuning: setting %s (id=0x%08x) -> %d", name, (unsigned) id, target);
  if (v4l2_set_int_(fd, id, target))
    ESP_LOGI(TAG, "image tuning: %-10s -> %4d  (range %ld..%ld, default %ld)", name, target,
             (long) q.minimum, (long) q.maximum, (long) q.default_value);
  else
    ESP_LOGW(TAG, "image tuning: failed to set %s -> %d: %s", name, target, strerror(errno));
}

// Set one ISP control to an absolute value (clamped to its queried range). Used
// for the white-balance channel gains, where the meaningful value is a specific
// gain (value = gain x 1000), not a fraction of the range.
void ESPVideoCamera::tune_ctrl_abs_(int fd, uint32_t id, int32_t value, const char *name) {
  struct v4l2_query_ext_ctrl q;
  if (!v4l2_query_ext_(fd, id, &q)) {
    ESP_LOGW(TAG, "image tuning: '%s' not supported by ISP", name);
    return;
  }
  if (value < (int32_t) q.minimum)
    value = (int32_t) q.minimum;
  if (value > (int32_t) q.maximum)
    value = (int32_t) q.maximum;
  ESP_LOGI(TAG, "image tuning: setting %s (id=0x%08x) -> %d", name, (unsigned) id, value);
  if (v4l2_set_int_(fd, id, value))
    ESP_LOGI(TAG, "image tuning: %-12s -> %4d  (range %ld..%ld)", name, value, (long) q.minimum,
             (long) q.maximum);
  else
    ESP_LOGW(TAG, "image tuning: failed to set %s -> %d: %s", name, value, strerror(errno));
}

// Auto-configure the camera for a bright, clear, colour-accurate image.
//
// The ISP exposes brightness/contrast/saturation/hue as post-processing
// controls on its own device node (ISP1 = /dev/video20), independent of the CSI
// capture device. We nudge brightness/contrast/saturation slightly above neutral
// and keep hue neutral. Exposure + gain are deliberately left on the ISP's
// automatic loop (AE/AGC) so the image stays correctly exposed as lighting
// changes — pinning a fixed exposure would make a "clear" image worse — and
// white balance stays on AWB for colour accuracy. esp_video reference-counts
// device opens, so opening the ISP node here (while the IPA pipeline also holds
// it) just bumps the refcount; our close() leaves the pipeline's handle intact.
void ESPVideoCamera::apply_image_tuning_() {
  // Run exactly once, ever. The ISP device is shared with the ISP/IPA pipeline
  // controller, which concurrently issues its own control ioctls; doing our own
  // ISP I/O more than once (or enumerating controls in a loop) raced that
  // pipeline and deadlocked the stream task. Set the guard before any I/O so a
  // second caller bails immediately.
  if (this->image_tuned_)
    return;
  this->image_tuned_ = true;

  int fd = open(ESP_VIDEO_ISP1_DEVICE_NAME, O_RDWR);
  if (fd < 0) {
    ESP_LOGW(TAG, "image tuning: open(%s) failed (%s) — leaving ISP defaults",
             ESP_VIDEO_ISP1_DEVICE_NAME, strerror(errno));
    return;
  }

  // Apply the 'bright, clear, colour-accurate' profile in one short pass. Ranges
  // are confirmed on this ISP: brightness [-128,127] def 0, contrast/saturation
  // [0,255] def 128, hue [0,360] def 0. Each step logs before it acts so a fault
  // localises to the last line printed. (No control enumeration here — walking
  // the control list on the live ISP raced the IPA pipeline and hung.)
  ESP_LOGI(TAG, "image tuning: applying profile on %s (fd=%d)", ESP_VIDEO_ISP1_DEVICE_NAME, fd);
  // Eased from +25 / 143: the brightness+contrast boost stretched highlights and
  // amplified sensor grain on white surfaces, which the H.264 encoder then blocked
  // on. Keep brightness just above neutral and contrast neutral to stop amplifying
  // grain; saturation stays up for colour pop, white balance unchanged.
  this->tune_ctrl_(fd, V4L2_CID_BRIGHTNESS, 0.53f, false, "brightness");  // ~+7
  this->tune_ctrl_(fd, V4L2_CID_CONTRAST, 0.50f, false, "contrast");      // 128 (neutral)
  this->tune_ctrl_(fd, V4L2_CID_SATURATION, 0.56f, false, "saturation");
  this->tune_ctrl_(fd, V4L2_CID_HUE, 0.0f, true, "hue");

  // White balance: the raw Bayer sensor has ~2x green photosites, so without
  // correction the image goes green (white reads green). The OV02C10's auto-WB
  // isn't compensating, so set a manual baseline that boosts the red + blue
  // channel gains (value = gain x 1000; green is the 1.0 reference). Tunable —
  // raise/lower per the live image. Range on this ISP is 1..3999.
  this->tune_ctrl_abs_(fd, V4L2_CID_RED_BALANCE, 1500, "red balance");
  this->tune_ctrl_abs_(fd, V4L2_CID_BLUE_BALANCE, 1600, "blue balance");

  close(fd);
  ESP_LOGI(TAG, "image tuning: done (gain/exposure left on AE/AGC auto; manual R/B white balance)");
}

void ESPVideoCamera::handle_rtsp_client_(int fd) {
  char req[1280];
  char resp[1400];
  while (true) {
    int n = recv(fd, req, sizeof(req) - 1, 0);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
      continue;  // recv timeout: healthy idle control conn during streaming — keep it
    if (n <= 0)
      break;     // clean close or keepalive-detected dead peer — free the accept loop
    req[n] = 0;
    int cseq = 0;
    const char *cs = strstr(req, "CSeq:");
    if (cs == nullptr)
      cs = strstr(req, "Cseq:");
    if (cs != nullptr)
      cseq = atoi(cs + 5);

    if (strncmp(req, "OPTIONS", 7) == 0) {
      snprintf(resp, sizeof(resp),
               "RTSP/1.0 200 OK\r\nCSeq: %d\r\nPublic: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n\r\n", cseq);
      this->locked_send_(resp, strlen(resp));
    } else if (strncmp(req, "DESCRIBE", 8) == 0) {
      if (!this->ensure_params_()) {
        snprintf(resp, sizeof(resp), "RTSP/1.0 500 Internal Server Error\r\nCSeq: %d\r\n\r\n", cseq);
        this->locked_send_(resp, strlen(resp));
        continue;
      }
      struct sockaddr_in sa;
      socklen_t sl = sizeof(sa);
      getsockname(fd, (struct sockaddr *) &sa, &sl);
      char sdp[800];
      this->build_sdp_(sdp, sizeof(sdp), ntohl(sa.sin_addr.s_addr));
      snprintf(resp, sizeof(resp),
               "RTSP/1.0 200 OK\r\nCSeq: %d\r\nContent-Type: application/sdp\r\nContent-Length: %u\r\n\r\n%s",
               cseq, (unsigned) strlen(sdp), sdp);
      this->locked_send_(resp, strlen(resp));
    } else if (strncmp(req, "SETUP", 5) == 0) {
      this->rtp_seq_ = 0;
      const char *itl = strstr(req, "interleaved=");
      if (itl != nullptr) {
        // RTP/AVP/TCP: stream RTP interleaved on this control connection (what
        // ffmpeg / HA's stream component request by default).
        this->rtp_over_tcp_ = true;
        this->rtp_tcp_channel_ = (uint8_t) atoi(itl + 12);
        snprintf(resp, sizeof(resp),
                 "RTSP/1.0 200 OK\r\nCSeq: %d\r\n"
                 "Transport: RTP/AVP/TCP;unicast;interleaved=%u-%u\r\n"
                 "Session: %08X\r\n\r\n",
                 cseq, (unsigned) this->rtp_tcp_channel_, (unsigned) (this->rtp_tcp_channel_ + 1),
                 (unsigned) this->rtsp_session_id_);
      } else {
        this->rtp_over_tcp_ = false;
        uint16_t cport = 0;
        const char *t = strstr(req, "client_port=");
        if (t != nullptr)
          cport = (uint16_t) atoi(t + 12);
        this->rtp_client_port_ = cport;
        struct sockaddr_in pa;
        socklen_t pl = sizeof(pa);
        getpeername(fd, (struct sockaddr *) &pa, &pl);
        this->rtp_client_ip_ = pa.sin_addr.s_addr;
        snprintf(resp, sizeof(resp),
                 "RTSP/1.0 200 OK\r\nCSeq: %d\r\n"
                 "Transport: RTP/AVP;unicast;client_port=%u-%u;server_port=6970-6971\r\n"
                 "Session: %08X\r\n\r\n",
                 cseq, cport, cport + 1, (unsigned) this->rtsp_session_id_);
      }
      this->locked_send_(resp, strlen(resp));
    } else if (strncmp(req, "PLAY", 4) == 0) {
      snprintf(resp, sizeof(resp),
               "RTSP/1.0 200 OK\r\nCSeq: %d\r\nSession: %08X\r\nRTP-Info: url=trackID=0;seq=%u\r\n\r\n", cseq,
               (unsigned) this->rtsp_session_id_, (unsigned) this->rtp_seq_);
      this->locked_send_(resp, strlen(resp));
      this->rtsp_playing_ = true;
    } else if (strncmp(req, "TEARDOWN", 8) == 0) {
      snprintf(resp, sizeof(resp), "RTSP/1.0 200 OK\r\nCSeq: %d\r\n\r\n", cseq);
      this->locked_send_(resp, strlen(resp));
      break;
    } else {
      snprintf(resp, sizeof(resp), "RTSP/1.0 501 Not Implemented\r\nCSeq: %d\r\n\r\n", cseq);
      this->locked_send_(resp, strlen(resp));
    }
  }
  this->rtsp_playing_ = false;
}

void ESPVideoCamera::rtsp_server_task_(void *arg) {
  auto *self = (ESPVideoCamera *) arg;
  // The network (lwIP/tcpip) stack may not be up yet when this task starts
  // (camera setup runs before WiFi). Retry the listen-socket setup until it
  // succeeds instead of giving up.
  int ls = -1;
  while (true) {
    ls = socket(AF_INET, SOCK_STREAM, 0);
    if (ls >= 0) {
      int yes = 1;
      setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
      struct sockaddr_in addr;
      memset(&addr, 0, sizeof(addr));
      addr.sin_family = AF_INET;
      addr.sin_addr.s_addr = htonl(INADDR_ANY);
      addr.sin_port = htons(self->rtsp_port_);
      if (bind(ls, (struct sockaddr *) &addr, sizeof(addr)) == 0 && listen(ls, 4) == 0)
        break;  // listening
      close(ls);
      ls = -1;
    }
    vTaskDelay(pdMS_TO_TICKS(1000));  // network not ready yet — retry
  }
  self->rtsp_listen_fd_ = ls;
  ESP_LOGI(TAG, "RTSP server listening on port %u (rtsp://<ip>:%u/cam)", self->rtsp_port_, self->rtsp_port_);
  while (true) {
    struct sockaddr_in client;
    socklen_t clen = sizeof(client);
    int fd = accept(ls, (struct sockaddr *) &client, &clen);
    if (fd < 0) {
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    ESP_LOGI(TAG, "RTSP: client connected");
    // Keep the single-threaded server from getting wedged on a stale/dead peer
    // (e.g. go2rtc reconnecting): bound the blocking recv and let TCP keepalive
    // detect a dead control connection (~25s) so the accept loop recovers.
    {
      int ka = 1, idle = 10, intvl = 5, cnt = 3;
      setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &ka, sizeof(ka));
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof(idle));
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof(intvl));
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &cnt, sizeof(cnt));
      struct timeval rcv_to = {};
      rcv_to.tv_sec = 60;
      setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &rcv_to, sizeof(rcv_to));
    }
    self->rtsp_client_fd_ = fd;
    self->handle_rtsp_client_(fd);
    self->rtp_over_tcp_ = false;
    self->rtsp_client_fd_ = -1;
    close(fd);
    ESP_LOGI(TAG, "RTSP: client disconnected");
  }
}

void ESPVideoCamera::start_rtsp_server_() {
  // Generous stacks: the control task runs ensure_params_ (start pipeline +
  // capture + H.264 encode + NAL parse) and the stream task runs encode + RTP.
  xTaskCreatePinnedToCore(rtsp_stream_task_, "rtsp_stream", 16384, this, 5, nullptr, 1);
  xTaskCreatePinnedToCore(rtsp_server_task_, "rtsp_ctrl", 16384, this, 5, nullptr, 1);
}

}  // namespace esphome::esp_video_camera

#endif  // USE_ESP_IDF
