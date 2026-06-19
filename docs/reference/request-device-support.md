---
title: Added Device Support
description:
  How to collect Home Assistant state information when asking EspControl to support a new device type.
---

# Added Device Support

EspControl can only support a new device type properly when we know how Home Assistant exposes it. The easiest way to help is to copy the device's state information from Home Assistant and share it in a GitHub issue.

Sorry, but I can't support additional screens because of the complexity of testing the existing range of devices.

## Find the Device State

1. Open **Home Assistant**.
2. Go to **Settings**.
3. Open **Developer Tools**.
4. Select the **States** tab.
5. Search for the device or entity you want EspControl to support.

## What to Share

When you find the right entity, [open an issue on GitHub](https://github.com/jtenniswood/espcontrol/issues/new) and include:

- The device or entity name.
- The entity ID, for example `sensor.living_room_air_quality`.
- The current **State** value.
- The **Attributes** shown for that entity.
- What you would like the panel to show or control.

If the device has more than one related entity, include each one that looks relevant. For example, a device might have one entity for status and another for battery, mode, or signal strength.

Screenshots are useful too, but please also paste the state and attribute text where possible. Text is easier to search, compare, and turn into firmware support.
