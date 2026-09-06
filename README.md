<h1 align="center">
  openqore
  <br>
  <img src=".github/open-qore-logo.png" width="40" alt="open-qore logo"> 
</h1>

an open-source toolkit to patch, modify, and enhance the firmware of soundcore q-series headphones, with future support for other models planned.

> **note:** this project is my personal journey into the world of hardware reverse-engineering and embedded systems. expect bugs, mistakes, and lots of fun. all contributions and advice are welcome!

<p align="center">
  <a href="https://github.com/nnonickreal/OpenQore"><img src="https://img.shields.io/badge/status-in%20development-orange?style=for-the-badge" alt="Status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/nnonickreal/OpenQore?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/nnonickreal/OpenQore/stargazers"><img src="https://img.shields.io/github/stars/nnonickreal/OpenQore?style=for-the-badge" alt="Stars"></a>
  <a href="https://github.com/nnonickreal/OpenQore/issues"><img src="https://img.shields.io/github/issues/nnonickreal/OpenQore?style=for-the-badge" alt="Issues"></a>
</p>

# important info! (fast navigation)
**do you want to:**

* patch your headphones' firmware? -> qorepatcher (this repository, look below for quick start)
* install / develop the custom firmware? (`soundcore devices based on bes2300p` only at the moment) -> [openqore SDK](https://github.com/nnonickreal/openqore-sdk)
* flash an update / install custom firmware over-the-air? (OTA) -> [OTA files for BES devices](https://github.com/nnonickreal/openqore/blob/main/docs/FIRMWARES.md) and [besota](https://github.com/nnonickreal/besota) - BES OTA flasher
* flash an update via UART / restore after a bad update or make a backup? -> [hardware flashing guide](https://github.com/nnonickreal/openqore/blob/main/docs/FLASH_MP.md)


i also created a demo project - a [DOOM port](https://github.com/nnonickreal/DOOMcore) based on the [DOOMBuds](https://github.com/arin-s/DOOMBuds) project. check that out too! =)
<h1 align="center">
  qorepatcher
</h1>

<p align="center">
  <strong><a href="INDEX.md">📚 read the full documentation 📚</a></strong>
</p>

## supported devices

this project was started with the soundcore life q35. if you want to help test or add support for a new model, please open an issue or DM me (read [contact](#contact--community))!

| model | chipset | status |
| :--- | :--- | :--- |
| soundcore life q35 | bes2300p | ✅ **supported** |
| soundcore life q30 | bes2300p | ❔ *should work but needs testing* |
| soundcore life tune pro | bes2300p | ❔ *should work but needs testing* |
| soundcore life tune | bes2300p | ❔ *should work but needs testing* |
| soundcore life q20i | bes2300p | ❔ *should work but needs testing* |
| soundcore space one | bes1502x | ❌ *WIP* |

**support for other models is a future goal!**

> **note:** devices based on the `bes2300*, bes1502*, bes1600` chipsets are the most likely candidates for future support.

## roadmap

- [x] initial firmware patcher for sound replacement.
- [x] unlock 48khz audio support for system sounds.
- [x] investigate and unlock stereo support for system sounds.
- [x] make patcher to work with all bes2300 chipsets (**warning! needs testing**)
- [ ] make patcher to work with all (or the most) bes chipsets
- [ ] implement patches for headphone name and mac address modification.
- [ ] remove the low volume limiter/gate on the aux input on some models (jack connection).
- [ ] create a user-friendly gui for the patcher.
- [x] reverse-engineer the ota (over-the-air) update protocol for wireless flashing.
- [ ] document the firmware structure and key functions.
- [ ] develop a library of community-created sound packs.

## quick start

this guide assumes you have `python` and `git` installed on your system.

**1. download and extract the zip archive from [releases](https://github.com/nnonickreal/openqore/releases/latest)**

**2. install dependencies**

the patcher requires ffmpeg for audio conversion and pybluez:

**windows:** 
```
pip install git+https://github.com/pybluez/pybluez.git
winget install ffmpeg
```

**macos:**
```
pip install git+https://github.com/pybluez/pybluez.git
brew install ffmpeg
```

**ubuntu/debian:**
```
pip install git+https://github.com/pybluez/pybluez.git
sudo apt install ffmpeg
```

**3. get your firmware file**

you can download the OTA image [here](https://github.com/nnonickreal/openqore/blob/main/docs/FIRMWARES.md) or read the flash with UART:

[➡️ hardware guide: connecting via UART](docs/FLASHING_MP.md)

reading the flash via ota (over-the-air) is planned for a future update. (if it's possible :D)

**4. congrats!**

you can find usage instructions [here](docs/USAGE.md)

## faq
<details>
  <summary>1. which option of the firmware (w/o ota boot or with it) in qorepatcher i should select?</summary>
<br>
  if you're patching the flash dump of the headphones, select the "with ota boot" option.
  
  if you have downloaded the ota update image from the official update servers, select the "without ota boot" option

  **note:** if you have patched the firmware without ota boot, you need to [add it on the header of the patched firmware](docs/OTABOOT.md) before [flashing via UART.](docs/FLASHING.md) you do **NOT** need this if you're using the [besota](https://github.com/nnonickreal/besota) script!
</details>

## technical deep dive

<details>
<summary>nerd stuff ahead: click to expand...</summary>

this section contains some of the initial findings from reverse-engineering the q35 firmware.

#### engineering modes and features
the headphones have several hidden test modes. some of them could be useful for future updates or debugging.

*   **engineering mode:** to enter this mode, hold the power button, connect the headphones to a pc via usb-c *before* they turn on, and wait for them to power up.

*   **rfcomm:** the headphones have two serial com ports. one of them is writable. so far, the only function i've managed to trigger through this port is a **UFR** (explanation below). this seems to be a security measure, as it was triggered by brute-forcing various hex codes rather than a specific command. interestingly, this is not the standard reset (power + vol+) but something different.

*   **testing mode:** this mode is entered similarly to engineering mode, but you need to release the power button immediately after the white led flashes for the first time, **before the blue light**. the headphones will then appear on the pc as a device with a "device descriptor request failed" error. you can confirm you're in this mode by the white led, which blinks faster than usual. its purpose is likely related to firmware flashing. also, in this mode, the headphones can be powered on while charging!

*   **undocumented factory reset (UFR):** a hidden factory reset can be triggered by rapidly pressing the power button multiple times while the headphones are powered on or sending something on virtual com port (above). this will cause the firmware to hang and then force a reboot after about 30 seconds. note that this action will erase all user data, including equalizer settings, bluetooth pairings, and other configurations.

#### firmware structure
the firmware appears to be a monolithic binary divided into multiple sections. each critical section is protected by a `CRC32` checksum. future patchers will automatically recalculate these checksums after any modification to prevent boot failures.

#### audio system
*   **stock:** the original system sounds are stored as `16khz, mono, SBC` audio streams.
*   **modded:** by patching the functions responsible for initializing the audio dac, it's possible to force the system to play back audio at `48khz, stereo`. this significantly improves the quality of custom sounds.

#### key components & interfaces
*   **chipset:** the heart of the q35 is a bestechnic (bes, best) `bes2300p` SoC. a datasheet can be found with searching.
*   **debug port:** a `uart` serial port is available on the pcb, which was used for initial debugging and is the primary method for unbricking a device after a bad flash. the `bes2300p` chip itself has two uart ports, but only one of them is exposed as easily accessible pads on the pcb.

</details>

## contributing

contributions are what make the open source community such an amazing place to learn, inspire, and create. any contributions you make are **greatly appreciated**.

if you have a suggestion that would make this better, please fork the repo and create a pull request. you can also simply open an issue with the tag "enhancement".
don't forget to give the project a star! thanks again!

1.  fork the project.
2.  create your feature branch (`git checkout -b feature/amazing-feature`).
3.  commit your changes (`git commit -m 'feat: add some amazing feature'`).
4.  push to the branch (`git push origin feature/amazing-feature`).
5.  open a pull request.

## contact & community

<p align="left">
  <a href="https://t.me/nnonick" target="_blank"><img src="https://img.shields.io/badge/telegram-%40nnonick-2CA5E0?style=for-the-badge&logo=telegram" alt="telegram"></a>
  <a href="https://discord.gg/EPjhKzUHVq" target="_blank"><img src="https://img.shields.io/badge/discord-join_chat-5865F2?style=for-the-badge&logo=discord" alt="discord server"></a>
</p>

## ❤️ support the project

if you find this project helpful and want to support its future development, you can treat me to a coffee or some snacks via boosty! every contribution is greatly appreciated and helps me dedicate more time to openqore.

<p>
  <a href="https://boosty.to/nnonick" target="_blank"><img src="https://img.shields.io/badge/support_me_on-boosty-FF8100?style=for-the-badge&logo=boosty&logoColor=white" alt="Boosty"></a>
</p>

## acknowledgements

this project was brought to life with the extensive use of ai-powered coding assistants. while the core reverse-engineering, research, and architectural decisions were made by the author, ai played a crucial role in accelerating the development process, writing boilerplate code, and debugging.

this is a modern project built with modern tools.

## license

this project is licensed under the MIT license. you can find the full license text in the [license](LICENSE) file.
