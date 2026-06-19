---
title: Contributing
description:
  How to get started if you want to contribute fixes, card types, firmware changes, or web configurator improvements to EspControl.
---

# Contributing

Thanks for wanting to help improve EspControl.

The project has a developer guide for people who want to work on the firmware,
the web configurator, docs, or generated files:

[Read the EspControl developer reference on GitHub](https://github.com/jtenniswood/espcontrol/blob/main/dev-docs/README.md)

Those pages cover the project layout, local build tools, how the web
configurator is bundled, how to flash a development build, where logs come from,
and what needs to change when adding or fixing a card type.

## Before Opening a Pull Request

- Check the existing [issues](https://github.com/jtenniswood/espcontrol/issues)
  to see if the change is already being discussed.
- Keep the change focused so it is easier to test.
- Run the checks listed in the developer reference before submitting code
  changes.
- If your change affects how users configure or use the panel, update the docs
  in this site too.

For small fixes, a pull request is usually enough. For larger changes, especially
new card types or firmware behaviour, opening an issue first makes it easier to
agree what should change before you spend time building it.
