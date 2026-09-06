# reading the flash (universal guide)

alright, you've [connected to the uart interface.](FLASH_MP.md#)

first of all, you need to install [bestool-ng](https://github.com/nnonickreal/bestool-ng).

**the next instructions are simple:**
1. turn off the headphones.
2. run bestool with the read-image option, specifying your uart adapter and offsets (will be available soon)
3. bestool should indicate that the chip is in the bootloader mode and that the flash memory is being read

congratulations! you have read the flash and are ready to start [flashing](https://github.com/nnonickreal/openqore/blob/main/docs/FLASHING.md) c:
