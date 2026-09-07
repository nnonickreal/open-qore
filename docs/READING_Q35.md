# reading the flash (Q35)

alright, you've [connected to the uart interface.](FLASH_MP.md)
first of all, you need to install [bestool-ng](https://github.com/nnonickreal/bestool-ng).

**the next instructions are simple:**
1. turn off the headphones.
2. run bestool with the read-image option, specifying your uart adapter
3. hold the anc button and do **not** release it until the flash has been read (bestool should indicate that the headphones are in the bootloader mode and that the flash memory is being read)
4. if you have released the button, then reset the headphones by connecting the type-c cable and try again

congratulations! you have read the flash and are ready to start [flashing](https://github.com/nnonickreal/openqore/blob/main/docs/FLASHING_Q35.md) c:
