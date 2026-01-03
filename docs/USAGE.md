# usage guide

this guide will walk you through the process of patching your firmware using the openqore toolkit.

## replacing system sounds

currently, the primary feature of openqore is replacing the stock system sounds. here's how to do it:

### prerequisites

1.  **your firmware dump:** currently, you must have a [firmware dump](UART_CONNECT.md) from your **own** pair of headphones. flashing a firmware dump from another device can lead to malfunctions, as some calibration data is unique to each unit.
2.  **custom sounds:** prepare the audio files you want to use. they can be in `.wav`.

    > **tip:** you can use my custom minimal sound pack available in the `sound_packs` directory as a starting point!

### step 1: prepare your files

1.  **rename your firmware:** rename your firmware dump file to `firmware.bin` and place it in the main project directory.
2.  **name your sounds:** you must name your custom sound files according to the following id table. the patcher uses these exact filenames to know which sound to replace.

    | filename | description |
    | :--- | :--- |
    | `ID_00.wav` | power on |
    | `ID_01.wav` | power off |
    | `ID_13.wav` | pairing mode / device disconnected |
    | `ID_15.wav` | successfully connected |
    | `ID_23.wav` | low battery |
    | `ID_29.wav` | maximum volume warning |
    | `ID_37.wav` | battery fully charged (battery high) |
    | `ID_38.wav` | battery medium |
    | `ID_40.wav` | anc on |
    | `ID_41.wav` | anc off (normal mode) |
    | `ID_42.wav` | transparency mode |

3.  **create the sounds directory:** create a folder named `sounds_src` in the main project directory and place all your named sound files inside it.

### step 2: run the patcher

1.  open your terminal in the `open-qore/patcher` directory.
2.  run the script using the command:
    ```
    python open-qore.py
    ```
3.  you will be presented with a menu of available patches. select the option for "patch audio prompts" by entering its corresponding number and pressing enter.

### step 3: enjoy!

congratulations! a new file named `firmware_patched.bin` has been created. this new firmware contains your custom sounds, and the system audio sample rate has been upgraded from 16khz to 48khz for improved quality.

now you can [flash](FLASHING.md) this file back to your headphones.
