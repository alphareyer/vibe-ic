* ldo_open — analog block netlist (ldo class)
* topology: NMOS-input five-transistor OTA driving a PMOS series pass device, closed by a resistive feedback divider, Miller-compensated
* _provenance: producer=analog_a3_netlist_emit schema=1
* _provenance: topology_ir=phase3/analog/ldo_open/topology.json sha256=2c7d82b96611f10657d24fcaf6a33e47d6c7162e49526dac6d2f1243d40f6c2a content_sha256=e60fc5451553aadfe3d1cc0a56ea4b853de3cb9c7c8ec9828c3f5fb1a8224935
* _provenance: spec=phase3/analog/ldo_open/spec.json sha256=d880c51b22e84223c31681aa947246f5743f9fe08bb4cacd5668880b81f64878
* _provenance: spec_values_bound=['dropout', 'dropout_max', 'iout', 'iout_max', 'iout_min', 'iq', 'iq_max', 'psrr', 'psrr_min', 'vin', 'vin_max', 'vin_min', 'vout', 'vout_max', 'vout_min']
* _provenance: design_content=structure_only
* _provenance: spec_bound_params=none
* _provenance: library_nominal_params=['cc.l', 'cc.w', 'mn1.l', 'mn1.w', 'mn2.l', 'mn2.w', 'mn_bias.l', 'mn_bias.w', 'mn_tail.l', 'mn_tail.w', 'mp1.l', 'mp1.w', 'mp2.l', 'mp2.w', 'mp_pass.l', 'mp_pass.m', 'mp_pass.w', 'r1.l', 'r1.w', 'r2.l', 'r2.w', 'r_bias.l', 'r_bias.w']
* _provenance: knobs_defaulted_by_library=['divider_ratio']
* _provenance: pdk_family=sky130A
* _provenance: model_lib=/foss/pdks/sky130A/libs.tech/ngspice/sky130.lib.spice section=tt
* _provenance: role_models={'cap': 'sky130_fd_pr__cap_mim_m3_1', 'nmos': 'sky130_fd_pr__nfet_01v8', 'pmos': 'sky130_fd_pr__pfet_01v8', 'res': 'sky130_fd_pr__res_high_po_0p35'}
* _provenance: voltage_domain=volts=2.0 elevated=False (stated by this block's spec; the device flavour above is elected FOR IT)
* _provenance: simulation_verified=False status=NOT_ATTEMPTED
* _provenance: run_ref=2ba06d6e7b1e
* _provenance: content_sha256=ea8a584935695fe37bd7b5697ff0845d42992de7b01968705fcbd52eca7a2ec7
* _provenance: provenance_ref=2ba06d6e7b1e/phase3/analog/ldo_open/ldo_open.sp@ea8a58493569
* _provenance: ai_handoff=skill:analog-sizing reason=23 device parameter(s) carry the topology library's nominal value because no bound spec value determines them; solving them against the spec is sizing judgment
* _provenance: device geometry above is the topology library nominal EXCEPT the spec_bound_params listed; sizing to the bound spec is skill `analog-sizing`, not this producer
* _provenance: transient_rail_measurement=none (this deck runs no transient, so there is no transient to measure; its operating point is still graded by `dc_op_rail_excursions`)
*
.option scale=1u
.lib /foss/pdks/sky130A/libs.tech/ngspice/sky130.lib.spice tt

.subckt ldo_open vdd vss vref vout
xmn_bias nbias nbias vss vss sky130_fd_pr__nfet_01v8 w=2 l=2
xr_bias vdd nbias vss sky130_fd_pr__res_high_po_0p35 w=0.35 l=60
xmn_tail ntail nbias vss vss sky130_fd_pr__nfet_01v8 w=4 l=2
xmn1 nd1 vfb ntail vss sky130_fd_pr__nfet_01v8 w=8 l=1
xmn2 vg vref ntail vss sky130_fd_pr__nfet_01v8 w=8 l=1
xmp1 nd1 nd1 vdd vdd sky130_fd_pr__pfet_01v8 w=4 l=1
xmp2 vg nd1 vdd vdd sky130_fd_pr__pfet_01v8 w=4 l=1
xmp_pass vout vg vdd vdd sky130_fd_pr__pfet_01v8 w=5 l=0.5 m=20
xcc vg vout sky130_fd_pr__cap_mim_m3_1 w=10 l=10
xr1 vout vfb vss sky130_fd_pr__res_high_po_0p35 w=0.35 l=20
xr2 vfb vss vss sky130_fd_pr__res_high_po_0p35 w=0.35 l=20
.ends ldo_open
