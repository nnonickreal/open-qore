# flashing guide (Q35)

first of all, you need to install [bestool-ng](https://github.com/nnonickreal/bestool-ng) and [**read (backup) the flash!**](https://github.com/nnonickreal/openqore/blob/main/docs/READING_Q35.md)

the steps are just as simple as the reading guide:
1. turn off the headphones
2. run bestool with the write-image option, specifying your uart adapter
3. hold the anc button and do not release it until the flash has been written (bestool should indicate that the headphones are in the bootloader mode and the firmware is writing)
4. if you have released the button, then reset the headphones by connecting the type-c cable and try again

congratulations! enjoy the update or your experiments! c:
