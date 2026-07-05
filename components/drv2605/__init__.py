"""DRV2605L haptic driver (TI) — minimal ESPHome I2C component.

Fires the chip's built-in ROM effect library. Exposes a `play(effect)` method
callable from lambdas (e.g. an LVGL button on_press or the touchscreen on_touch)
so taps get a haptic click. Written for the Pimoroni DRV2605L LRA breakout
(on-board linear resonant actuator, I2C address 0x5A) but supports ERM too.
"""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import i2c
from esphome.const import CONF_ID

DEPENDENCIES = ["i2c"]
CODEOWNERS = ["@bdw547"]

drv2605_ns = cg.esphome_ns.namespace("drv2605")
DRV2605Component = drv2605_ns.class_("DRV2605Component", cg.Component, i2c.I2CDevice)

CONF_ACTUATOR = "actuator"
ACTUATORS = {"lra": True, "erm": False}

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(DRV2605Component),
            cv.Optional(CONF_ACTUATOR, default="lra"): cv.enum(ACTUATORS, lower=True),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(i2c.i2c_device_schema(0x5A))
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)
    cg.add(var.set_lra(config[CONF_ACTUATOR]))
