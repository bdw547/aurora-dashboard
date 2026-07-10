"""MJPEG stream viewer for ESP32-P4 LVGL panels.

Pulls an MJPEG (multipart/x-mixed-replace) stream from a Home Assistant
camera proxy URL on a dedicated FreeRTOS task, hardware-decodes the JPEG
frames (esp_driver_jpeg), hardware-scales them with the PPA into
double-buffered RGB565 PSRAM buffers, and presents the frames to an LVGL
image widget from the ESPHome main loop.

Also exposes a lambda-only "stills channel" (no config keys):
`id(x).fetch_still(url, widget, w, h)` queues a one-shot JPEG fetch (e.g.
album art) serviced by the same task/decoder/PPA between stream frames,
presented into per-widget PSRAM buffers from the main loop.

esp_driver_jpeg and esp_driver_ppa are core ESP-IDF 5.5 components on the
esp32p4 — no managed components are required.
"""

from esphome import automation
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import (
    CONF_HEIGHT,
    CONF_ID,
    CONF_TRIGGER_ID,
    CONF_URL,
    CONF_WIDTH,
)

CODEOWNERS = ["@ben-abeo"]
DEPENDENCIES = ["esp32", "network", "lvgl"]
MULTI_CONF = True

mjpeg_stream_ns = cg.esphome_ns.namespace("mjpeg_stream")
MJPEGStream = mjpeg_stream_ns.class_("MJPEGStream", cg.Component)
StreamState = mjpeg_stream_ns.enum("StreamState", is_class=True)
StateTrigger = mjpeg_stream_ns.class_(
    "StateTrigger", automation.Trigger.template(StreamState)
)

CONF_MAX_FPS = "max_fps"
CONF_MAX_JPEG_SIZE = "max_jpeg_size"
CONF_MAX_SOURCE_WIDTH = "max_source_width"
CONF_MAX_SOURCE_HEIGHT = "max_source_height"
CONF_TARGETS = "targets"
CONF_TASK_CORE = "task_core"
CONF_TASK_PRIORITY = "task_priority"
CONF_READ_TIMEOUT = "read_timeout"
CONF_ON_STATE = "on_state"

TARGET_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_WIDTH): cv.int_range(min=16, max=1920),
        cv.Required(CONF_HEIGHT): cv.int_range(min=16, max=1920),
    }
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(MJPEGStream),
        cv.Optional(CONF_URL, default=""): cv.string,
        cv.Optional(CONF_MAX_FPS, default=8.0): cv.float_range(min=0.5, max=15.0),
        cv.Optional(CONF_MAX_JPEG_SIZE, default="512KB"): cv.validate_bytes,
        cv.Optional(CONF_MAX_SOURCE_WIDTH, default=1920): cv.int_range(
            min=16, max=4096
        ),
        cv.Optional(CONF_MAX_SOURCE_HEIGHT, default=1080): cv.int_range(
            min=16, max=4096
        ),
        # The list index is the target id passed to start(target_idx, widget).
        cv.Required(CONF_TARGETS): cv.All(
            cv.ensure_list(TARGET_SCHEMA), cv.Length(min=1)
        ),
        cv.Optional(CONF_TASK_CORE, default=1): cv.int_range(min=0, max=1),
        cv.Optional(CONF_TASK_PRIORITY, default=4): cv.int_range(min=1, max=20),
        cv.Optional(
            CONF_READ_TIMEOUT, default="10s"
        ): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_ON_STATE): automation.validate_automation(
            {
                cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(StateTrigger),
            }
        ),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_url(config[CONF_URL]))
    cg.add(var.set_max_fps(config[CONF_MAX_FPS]))
    cg.add(var.set_max_jpeg_size(config[CONF_MAX_JPEG_SIZE]))
    cg.add(
        var.set_max_source_size(
            config[CONF_MAX_SOURCE_WIDTH], config[CONF_MAX_SOURCE_HEIGHT]
        )
    )
    for target in config[CONF_TARGETS]:
        cg.add(var.add_target(target[CONF_WIDTH], target[CONF_HEIGHT]))
    cg.add(var.set_task_core(config[CONF_TASK_CORE]))
    cg.add(var.set_task_priority(config[CONF_TASK_PRIORITY]))
    cg.add(var.set_read_timeout(config[CONF_READ_TIMEOUT]))

    for conf in config.get(CONF_ON_STATE, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(StreamState, "state")], conf)
