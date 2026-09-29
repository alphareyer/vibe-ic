// pdk_registry.mjs — the PDK facts mcp-eda uses come from the flow's own
// programs/pdk_registry.json, never from a second table in this server.
//
// WHY (audit §3.23-1, R-0929-TOOL-DEFAULT): index.js carried its own pdkConfig
// table, and it had drifted from the registry the flow reads. gf180 selected
// gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib, while the registry pins the 5 V
// library's nominal sign-off corner tt_025C_5v00 (its own comment: `lib/*tt*.lib`
// matches 1v80/3v30/5v00 and only 5v00 is the rail of this mcu7t5v0 library).
// Every mcp-eda synth / STA / PnR / IR run on gf180 therefore used a different
// library than the flow. One source of truth closes that class.
//
// The registry names its assets as paths relative to the PDK root, some as a
// glob. A glob is resolved INSIDE the EDA container (the PDK lives there) and
// must match exactly one file; zero or several is a named error, never a guess.
//
// Fields the registry does not carry (the power pin / net names and, for some
// PDKs, the antenna diode master) stay here as ELECTRICAL; they are electrical
// conventions of the cell library, not asset or corner choices.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const REGISTRY_PATH = resolve(HERE, "..", "..", "..", "programs", "pdk_registry.json");

// HARVESTED from the table this replaces (index.js pdkConfig, pre-cut):
//  * vdd_net / vss_net are the PDN NET names eda_pnr creates, which are NOT the
//    std-cell PIN names in vdd_pin: eda_pnr writes `add_global_connection -net
//    <vdd_net> -pin_pattern "<vdd_pin>"`, so on sky130 the DEF SPECIALNETS are
//    VDD/VSS while the cell pins are VPWR/VGND. A consumer that needs a NET
//    (OpenROAD PSM `analyze_power_grid -net`) must read the net, not the pin.
//  * antenna_diode_cell (v1.3.53 R9) is the PDK's own diode master, data not
//    logic, consumed by antennaRepairTcl; the registry's value wins (gf180
//    carries it), this table fills the PDKs whose entry does not.
const ELECTRICAL = {
  gf180: { vdd_pin: "VDD", vss_pin: "VSS", vdd_net: "VDD", vss_net: "VSS" },
  sky130: { vdd_pin: "VPWR", vss_pin: "VGND", vdd_net: "VDD", vss_net: "VSS",
            antenna_diode_cell: "sky130_fd_sc_hd__diode_2" },
  nangate45: { vdd_pin: "VDD", vss_pin: "VSS", vdd_net: "VDD", vss_net: "VSS",
               antenna_diode_cell: "ANTENNA_X1" },
};

let _cache = null;
function loadRegistry(path = REGISTRY_PATH) {
  if (path === REGISTRY_PATH && _cache) return _cache;
  const doc = JSON.parse(readFileSync(path, "utf8"));
  const pdks = Array.isArray(doc.pdks) ? doc.pdks : [];
  if (path === REGISTRY_PATH) _cache = pdks;
  return pdks;
}

/** The registry entry whose `per_pdk_table_key` is `key` (gf180, sky130, ...). */
export function registryEntry(key, path = REGISTRY_PATH) {
  return loadRegistry(path).find((e) => e && e.per_pdk_table_key === key) || null;
}

/** The mcp-eda PDK config of `key`, built from the registry. Throws
 * PDK_NOT_IN_REGISTRY when the registry has no such PDK or lacks an asset. */
export function registryPdkConfig(key, pdkRoot, path = REGISTRY_PATH) {
  const e = registryEntry(key, path);
  if (!e) {
    throw new Error(`PDK_NOT_IN_REGISTRY: no programs/pdk_registry.json entry has per_pdk_table_key=${key}`);
  }
  const missing = ["liberty_glob", "tech_lef_glob", "site", "metal_prefix"].filter((k) => !e[k]);
  if (missing.length) {
    throw new Error(`PDK_NOT_IN_REGISTRY: ${e.name} lacks ${missing.join(", ")}`);
  }
  // libs.ref/<scl>/lib/... — the standard-cell library the Liberty belongs to.
  const scl = String(e.liberty_glob).split("/")[1];
  const el = ELECTRICAL[key] || {};
  return {
    registry_name: e.name,
    pdk_path: `${pdkRoot}/${e.name}`,
    scl,
    liberty_rel: e.liberty_glob,
    techlef_rel: e.tech_lef_glob,
    site: e.site,
    metal_prefix: e.metal_prefix,
    vdd_pin: el.vdd_pin, vss_pin: el.vss_pin,
    vdd_net: el.vdd_net, vss_net: el.vss_net,
    antenna_diode_cell: e.antenna_diode_cell || el.antenna_diode_cell || null,
  };
}

/** `<pdk_path>/<rel>`; a glob is resolved by `resolveGlob(absGlob) -> string[]`
 * (inside the container) and must match exactly one file. */
export function assetPath(cfg, rel, resolveGlob) {
  const p = `${cfg.pdk_path}/${rel}`;
  if (!p.includes("*")) return p;
  const hits = (resolveGlob ? resolveGlob(p) : []).filter(Boolean);
  if (hits.length !== 1) {
    throw new Error(`PDK_ASSET_GLOB_NOT_UNIQUE: ${p} matched ${hits.length} file(s)`
      + (hits.length ? ` (${hits.slice(0, 4).join(", ")})` : "")
      + " in the EDA container; the registry glob must name exactly one");
  }
  return hits[0];
}
