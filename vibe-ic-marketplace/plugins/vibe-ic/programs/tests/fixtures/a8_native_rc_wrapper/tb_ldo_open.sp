* tb_ldo_open — stimulus for the A3 block netlist
* _provenance: producer=analog_a3_netlist_emit schema=1
* _provenance: topology_ir=phase3/analog/ldo_open/topology.json sha256=bcd736dbf4e30bab900eb3d3c1bbdb815578c9802c4c640f21c921ca2b79a40c content_sha256=e60fc5451553aadfe3d1cc0a56ea4b853de3cb9c7c8ec9828c3f5fb1a8224935
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
* _provenance: ai_handoff=skill:analog-sizing reason=23 device parameter(s) carry the topology library's nominal value because no bound spec value determines them; solving them against the spec is sizing judgment
* _provenance: device geometry above is the topology library nominal EXCEPT the spec_bound_params listed; sizing to the bound spec is skill `analog-sizing`, not this producer
* _provenance: transient_rail_measurement=none (this deck runs no transient, so there is no transient to measure; its operating point is still graded by `dc_op_rail_excursions`)
*
* condition: supply = 1.8 V (bound Vin if the spec carries one, else the bound Vout plus 1.5 V of headroom — a testbench condition, not a spec)
* condition: reference = 0.6 V, implied by the bound Vout and the divider ratio
* condition: load = 1 kOhm to ground (testbench condition)
.include ldo_open.sp
v_vdd vdd 0 1.8
v_vref vref 0 0.6
xdut vdd 0 vref vout ldo_open
r_load vout 0 1k
* the integration control this producer verifies under — see `_TB_INTEGRATION_OPTIONS`
.options trtol=1
.control
op
let vo = v(vout)
echo "MEAS vout=" $&vo
.endc
.end
