# Open sampled mixed-signal component input

This hand-declared component has a digital enable/status wrapper and a
functional RC receiver (1 kohm, 10 pF). The resistor/capacitor ARE the circuit;
they are not claimed to be extracted parasitics. No PDK or physical signoff
claim is made. Thresholds, clock, sample window and limits are supplied inputs.

The ordinary M3 producer must execute Icarus/VVP, drive ngspice with the actual
wrapper enable trajectory, then replay the measured receiver voltage through
the declared ADC thresholds into the SAME wrapper. Its registered status must
follow that signal. Physical timing/noise/crosstalk coverage is unavailable.
