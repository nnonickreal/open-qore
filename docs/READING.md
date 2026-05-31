# reading the flash (tested on bes2300p/1502x)

alright, you've connected to the uart interface.
first of all, you need to install the [bestool by ralim](https://github.com/ralim/bestool).

**the next instructions are simple:**
1. turn off the headphones.
2. run bestool with the read-image option, specifying your uart adapter
3. hold the anc button and do **not** release it until the flash has been read (bestool should indicate that the headphones are in the bootloader mode and that the flash memory is being read)
4. if you have released the button, then reset the headphones by connecting the type-c cable and try again

congratulations! you have read the flash and are ready to start [flashing](https://github.com/nnonickreal/openqore/blob/main/docs/FLASHING.md)!! c:
