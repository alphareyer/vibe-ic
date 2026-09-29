* calibration: ngspice native meas result rows (RC low-pass, pulse drive)
v1 in 0 pulse(0 1 0 1n 1n 5u 10u)
r1 in out 1k
c1 out 0 1n
.control
tran 10n 30u
meas tran cal_avg avg v(nosuch) from=10u to=29u
meas tran cal_max when v(out)=5
meas tran cal_at find v(out) at=50u
echo "MEAS cal_echo=" $&cal_avg
.endc
.end
