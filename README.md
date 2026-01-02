<h1 align="center">
  openqore
  <br>
  <img src=".github/open-qore-logo.png" width="50" alt="open-qore logo"> 
</h1>

an open-source toolkit to patch, modify, and enhance the firmware of soundcore q-series headphones, with future support for other models planned.

> **note:** this project is my personal journey into the world of hardware reverse-engineering and embedded systems. it was created by a teenager and i'm learning as i go. expect bugs, mistakes, and lots of fun. all contributions and advice are welcome!

<p align="center">
  <a href="https://github.com/nnonickreal/OpenQore"><img src="https://img.shields.io/badge/status-in%20development-orange?style=for-the-badge" alt="Status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/nnonickreal/OpenQore?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/nnonickreal/OpenQore/stargazers"><img src="https://img.shields.io/github/stars/nnonickreal/OpenQore?style=for-the-badge" alt="Stars"></a>
  <a href="https://github.com/nnonickreal/OpenQore/issues"><img src="https://img.shields.io/github/issues/nnonickreal/OpenQore?style=for-the-badge" alt="Issues"></a>
</p>

## supported devices

this project was started with the soundcore life q35. support for other devices is a community goal. if you want to help test or add support for a new model, please open an issue!

| model | chipset | status |
| :--- | :--- | :--- |
| soundcore life q35 | bes2300p | ✅ **supported** |
| soundcore life q30 | bes2300p | ❔ *untested, but should work* |
| soundcore life tune pro | bes2300p | ❔ *untested, but should work* |
| soundcore life tune | bes2300p | ❔ *untested, but should work* |

**support for other models is a future goal!**

> **note:** devices based on the `bes2300p` chipset are the most likely candidates for future support.

## technical deep dive

<details>
<summary>nerd stuff ahead: click to expand...</summary>

this section contains some of the initial findings from reverse-engineering the q35 firmware.

#### having fun with engineering modes
the headphones have several hidden test modes. some of them could be useful for future updates or debugging.

*   **engineering mode:** to enter this mode, hold the power button, connect the headphones to a pc via usb-c *before* they turn on, and wait for them to power up. once connected via bluetooth, the headphones will expose two serial com ports. one of them is writable. so far, the only function i've managed to trigger through this port is a factory reset. this seems to be a security measure, as it was triggered by brute-forcing various hex codes rather than a specific command. interestingly, this is not the standard reset (power + vol+) but something different.

*   **testing mode:** this mode is entered similarly to engineering mode, but you need to release the power button immediately after the white led flashes for the first time, *before the blue light*. the headphones will then appear on the pc as a device with a "device descriptor request failed" error. you can confirm you're in this mode by the white led, which blinks faster than usual. its purpose is likely related to firmware flashing. also, in this mode, the headphones can be powered on while charging!

#### firmware structure
the firmware appears to be a monolithic binary divided into multiple sections. each critical section is protected by a `crc32` checksum. future patchers will automatically recalculate these checksums after any modification to prevent boot failures.

#### audio system
*   **stock:** the original system sounds are stored as `16khz, mono, 16-bit PCM` audio streams, likely encoded with SBC for transport but stored raw.
*   **modded:** by patching the functions responsible for initializing the audio dac, it's possible to force the system to play back audio at `48khz`. this significantly improves the quality of custom sounds. stereo support is a work-in-progress (wip).

#### key components & interfaces
*   **chipset:** the heart of the q35 is a bestechnic (bes, best) `bes2300p` soc. a datasheet can be found with some googling.
*   **debug port:** a `uart` serial port is available on the pcb, which was used for initial debugging and is the primary method for unbricking a device after a bad flash. the `bes2300p` chip itself has two uart ports, but only one of them is exposed as easily accessible pads on the pcb.

</details>

## acknowledgements

this project was brought to life with the extensive use of ai-powered coding assistants (like claude and chatgpt). while the core reverse-engineering, research, and architectural decisions were made by the author, ai played a crucial role in accelerating the development process, writing boilerplate code, and debugging.

this is a modern project built with modern tools.
