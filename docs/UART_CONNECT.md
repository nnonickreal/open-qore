# connecting to the service port (uart)

> **⚠️ critical warning: your uart adapter must be set to 1.8 volts!** using a 3.3v or 5v adapter will permanently damage the headphones' soc.

the headphones have a uart port that allows direct communication with the system on a chip (soc). contact points (pads) are available on both the left and right earcups' pcbs. you can choose which side to disassemble, but this guide focuses on the left earcup.

### disassembly guide (left earcup)

disassembling the left earcup is straightforward:

1.  **remove the earpad:** start by gently detaching the earpad. there are many video guides on youtube for this.
2.  **remove the screws:** unscrew all visible screws to release the speaker assembly.
3.  **lift the speaker:** carefully lift the speaker driver. be extremely cautious as it is soldered to the main pcb with thin wires.
4.  **separate the housing:** unscrew and separate the plastic housing. the main cup is attached to the pcb-holding frame with strong clips, so you might need to apply gentle but firm pressure.
5.  **access the pcb:** once the housing is open, unscrew the main pcb, lift it out, and flip it over. the hardest part is done! :)

### pinout

here is the pinout for the tx and rx pads on the left earcup's pcb:

<img src="/.github/assets/uart_pins.jpg" width="200" alt="uart pins">

### removing the glue
**important:** the uart pads are covered with a layer of hot glue from the factory for insulation. you will need to carefully remove this glue before you can solder.

### wiring
once the pads are clean, connect them to your uart adapter in a crossover configuration:

*   headphone `tx` -> adapter `rx`
*   headphone `rx` -> adapter `tx`
*   **vcc:** do **not** connect the vcc pin! the headphones are powered by their own battery.
*   **gnd (ground):** for a reliable ground connection, it is recommended to solder the gnd wire to the metal shield of the usb-c port on the pcb.

congratulations, you're connected! if you want to explore the capabilities of this port, check out the [bestool project by ralim](https://github.com/Ralim/BesTool).
sending funny commands on this port is a WIP.
