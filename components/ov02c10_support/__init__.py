"""Aurora OV02C10 camera support.

The Guition JC1060P470 carries an on-board OmniVision **OV02C10** MIPI-CSI
sensor. We stream it via the `esp_video_camera` external component (Espressif
`esp_video` stack). That stack pulls `espressif/esp_cam_sensor` 2.2.* from the
component registry, but the public 2.2.0 release does **not** ship an OV02C10
driver (it has os02n10/os04c10/ov5647/... but not ov02c10).

This component injects a locally-vendored copy of `esp_cam_sensor` 2.2.0 with
the OV02C10 driver grafted in-tree (driver lifted from the vendor's
`esp_cam_sensor` 2.0.1 reference, which is source-identical to 2.2.0 for sibling
drivers — ov5647.c diffs 0 lines across those versions). It is registered as a
local ESP-IDF component via `path:`, which overrides the registry version that
`esp_video` would otherwise download, as long as the vendored version (2.2.0)
satisfies esp_video's `2.2.*` constraint.

Enable the driver itself via sdkconfig in the device YAML:
    CONFIG_CAMERA_OV02C10: "y"
    CONFIG_CAMERA_OV02C10_AUTO_DETECT_MIPI_INTERFACE_SENSOR: "y"
    CONFIG_CAMERA_OV02C10_MIPI_RAW10_1920x1080_30FPS: "y"
    CONFIG_CAMERA_OV02C10_DEFAULT_IPA_JSON_CONFIGURATION_FILE: "y"
"""

import os

import esphome.config_validation as cv
from esphome.components import esp32

CODEOWNERS = ["@bdw547"]
DEPENDENCIES = ["esp32"]

CONFIG_SCHEMA = cv.Schema({})

_VENDORED_ESP_CAM_SENSOR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "esp_cam_sensor"
)


async def to_code(config):
    # Override esp_video's registry esp_cam_sensor with our local copy that
    # includes the OV02C10 driver. A root `path:` dependency takes precedence
    # over the transitively-resolved registry version of the same component.
    esp32.add_idf_component(
        name="espressif/esp_cam_sensor",
        path=_VENDORED_ESP_CAM_SENSOR,
    )
