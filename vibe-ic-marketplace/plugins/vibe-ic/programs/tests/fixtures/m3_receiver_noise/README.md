# Open receiver/noise component input

This fixture extends the unchanged open R3 wrapper/functional RC receiver with
a separate, explicitly declared thermal-noise receiver: a 1 kohm resistor and
10 pF capacitor at 27 C. These elements ARE the circuit, not invented extracted
parasitics. All inputs are project-local, open and supplied with the fixture.
No proprietary model, PDK, extracted timing, crosstalk or physical claim is made.

The ordinary M3 producer preserves its real Icarus/VVP -> ngspice transient ->
ADC/RNM wrapper path and additionally runs native ngspice `.noise` over 1 Hz
through 1 GHz at a 0.5 V bias. Input criteria require 10..25 microvolt integrated
output RMS noise. The native spectral density and integrated scalar are both
saved and independently rederived by the existing strict SI consumer.
Noise may be MEASURED for this component. Timing/crosstalk remain NOT_MEASURED;
full M3/M4/tapeout readiness remains false. No expected output file is shipped.
