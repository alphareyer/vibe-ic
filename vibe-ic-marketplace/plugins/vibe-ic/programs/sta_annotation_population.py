"""Typed timing-annotation population; never infer exclusions from cell names."""
import hashlib
import re
from collections import defaultdict
from pathlib import Path


#: The typed class R-0915-128 grants: a driver that faces OFF the die, which a
#: SPEF extracted from the routed die cannot annotate by construction. Judging an
#: artefact for what it CAN contain (R-0915-124/125) -- not narrowing a
#: population to turn a row green.
OFF_DIE = 'OFF_DIE_DRIVER_NOT_IN_SPEF'


def classify(body, def_file, io_masters=None):
    """Typed annotation population. `io_masters` is the set of IO-cell master
    names from the run's own LEF inventory, used ONLY to recognise a pad cell's
    off-die terminal; absent, that class is unavailable and such a driver stays
    REQUIRED_OR_UNKNOWN, so an absent inventory can never widen what passes."""
    result = {'complete': False, 'drivers': [], 'reason': None}
    match = re.search(r'^Found (\d+) unannotated drivers\.\n(.*?)^Found (\d+) partially unannotated drivers\.$', body, re.M | re.S)
    if not match:
        result['reason'] = 'missing native annotation counts'
        return result
    drivers = [line.strip() for line in match[2].splitlines() if line.strip()]
    if len(drivers) != int(match[1]) or len(set(drivers)) != len(drivers) or int(match[3]) != 0:
        result['reason'] = 'inconsistent driver inventory or partial annotation'
        return result
    result.update(raw_missing=int(match[1]), raw_partial=int(match[3]))
    if not drivers:
        result['complete'] = True
        return result
    try:
        data = Path(def_file).read_bytes()
    except OSError:
        result['reason'] = 'DEF authority absent'
        return result
    result['def_sha256'] = hashlib.sha256(data).hexdigest()
    text = data.decode(errors='strict')
    normalize = lambda s: s.replace('\\', '').strip()
    bindings = defaultdict(list)
    wildcards = []
    net_uses = defaultdict(list)   # DEF net name -> every USE its statements declare
    regular = False
    for section in ('NETS', 'SPECIALNETS'):
        block = re.search(r'^'+section+r'\s+(\d+)\s*;(.*?)^END '+section+r'\b', text, re.M | re.S)
        if not block:
            if re.search(r'^'+section+r'\b', text, re.M):
                result['reason'] = 'malformed DEF net section'
                return result
            continue
        regular |= section == 'NETS'
        statements = list(re.finditer(r'^\s*-\s+(\S+)\s+(.*?);', block[2], re.M | re.S))
        if len(statements) != int(block[1]):
            result['reason'] = 'DEF net count mismatch'
            return result
        for statement in statements:
            uses = re.findall(r'\+\s+USE\s+(\w+)', statement[2])
            if len(uses) > 1:
                result['reason'] = 'ambiguous DEF USE declaration'
                return result
            value = (normalize(statement[1]), uses[0] if uses else 'UNDECLARED')
            net_uses[value[0]].append(value[1])
            for inst, pin in re.findall(r'\(\s*(\S+)\s+(\S+)\s*\)', statement[2].split('+')[0]):
                inst, pin = normalize(inst), normalize(pin)
                if inst == '*':
                    wildcards.append((pin, value))
                else:
                    bindings[pin if inst == 'PIN' else inst+'/'+pin].append(value)
    if not regular:
        result['reason'] = 'DEF regular net inventory absent'
        return result
    # CONDITION (a): the off-die judgement is DERIVED FROM THE DEF, never from a
    # name. The PINS section IS the design's statement of which drivers face off
    # the die; COMPONENTS is its statement of which instance carries which master.
    top_pins = set()
    pins_block = re.search(r'^PINS\s+(\d+)\s*;(.*?)^END PINS\b', text, re.M | re.S)
    if pins_block:
        top_pins = {normalize(n) for n in
                    re.findall(r'^\s*-\s+(\S+)\s', pins_block[2], re.M)}
        if len(top_pins) != int(pins_block[1]):
            result['reason'] = 'DEF pin count mismatch'
            return result
    masters = {}
    comp_block = re.search(r'^COMPONENTS\s+(\d+)\s*;(.*?)^END COMPONENTS\b',
                           text, re.M | re.S)
    if comp_block:
        for inst, master in re.findall(r'^\s*-\s+(\S+)\s+(\S+)', comp_block[2], re.M):
            masters[normalize(inst)] = normalize(master)
    # Which nets carry a top-level PIN connection. A pad terminal bonded to a
    # port sits on such a net; an INTERNAL driver on an OUTPUT port's net also
    # does, which is why net membership alone is NOT the test -- the instance's
    # master must also be an IO cell, from the LEF inventory.
    nets_with_top_pin = {net for pin, (net, _use) in
                         [(k, v[0]) for k, v in bindings.items() if v]
                         if pin in top_pins}
    io_masters = {normalize(m) for m in (io_masters or ())}
    linked = {line.rsplit(' ', 1)[0] for line in re.findall(r'^STA_LINK_INSTANCE (.+)$', body, re.M)}
    disconnected = set(re.findall(r'^STA_UNCONNECTED_OUTPUT (\S+)$', body, re.M))
    for driver in drivers:
        inst, pin = driver.rsplit('/', 1) if '/' in driver else (None, driver)
        evidence = list(bindings[driver])
        if inst in linked:
            evidence += [value for pattern, value in wildcards if pattern == pin]
        # A TOP-LEVEL PORT that is the design's own SUPPLY. A netlist written
        # with its supply nets as ports (a hard-macro view: `inout VDD;`)
        # makes STA list the port as an unannotated driver, and a DEF that
        # carries the supply only as a special net -- `- VDD ( * VDD ) + USE
        # POWER`, no `( PIN VDD )` and no PINS row -- binds nothing to it, so
        # it fell through to REQUIRED_OR_UNKNOWN and refused a complete
        # post-route PVT sweep (spm x gf180mcuD HARDMACRO, 2026-09-28: all
        # six FF/SS/TT sections measured, quarantined as `.attempt-*`). A
        # port and the net it drives share one name, so the DEF's own typing
        # of THAT net is the evidence: exactly one statement, USE POWER or
        # GROUND. Duplicate or conflicting typing stays unknown.
        #
        # That is an INFERENCE from a name match, not a DEF binding (review
        # wave 7): `def_bindings` keeps only what the DEF binds, and the
        # inference is recorded as its own field and basis, and counted.
        bound = list(evidence)
        inferred = None
        if inst is None and not evidence and len(net_uses.get(driver, ())) == 1:
            evidence = [(driver, net_uses[driver][0])]
            inferred = {'net': driver, 'use': net_uses[driver][0], 'statements': 1}
        if evidence and len(set(evidence)) == 1 and all(use in ('POWER', 'GROUND') for net, use in evidence):
            classification = 'EXPLICIT_PG_NOT_SIGNAL_PARASITICS'
        elif not evidence and inst in linked and driver in disconnected:
            classification = 'NATIVE_UNCONNECTED_OUTPUT'
        elif _off_die(driver, inst, evidence, top_pins, masters, io_masters,
                      nets_with_top_pin):
            classification = OFF_DIE
        else:
            classification = 'REQUIRED_OR_UNKNOWN'
        row = {'driver': driver, 'classification': classification,
               'def_bindings': bound}
        if inferred is not None and classification == 'EXPLICIT_PG_NOT_SIGNAL_PARASITICS':
            row['basis'] = 'supply_by_same_name_net'
            row['supply_by_same_name_net'] = inferred
        elif bound:
            row['basis'] = 'def_binding'
        result['drivers'].append(row)
    result['complete'] = all(row['classification'] != 'REQUIRED_OR_UNKNOWN'
                             for row in result['drivers'])
    # CONDITION (b): disclosed by name and count, so a reader sees what was
    # excluded and why without re-deriving it.
    off = [row['driver'] for row in result['drivers']
           if row['classification'] == OFF_DIE]
    pins_only = [d for d in off if '/' not in d]
    result['off_die_drivers'] = {
        'class': OFF_DIE, 'count': len(off),
        'top_level_pins': len(pins_only),
        'pad_terminals': len(off) - len(pins_only),
        'basis': ('the DEF PINS section for a top-level pin; the DEF COMPONENTS '
                  'master plus the run LEF inventory for a pad terminal'),
        'drivers': off,
        'disclosure': (
            f"{len(off)} off-die driver(s): {len(pins_only)} top-level pin(s) + "
            f"{len(off) - len(pins_only)} PAD terminal(s), not annotated by "
            f"construction"),
    }
    inferred_pg = [row['driver'] for row in result['drivers']
                   if row.get('basis') == 'supply_by_same_name_net']
    result['supply_by_same_name_net'] = {
        'count': len(inferred_pg), 'drivers': inferred_pg,
        'basis': ('a top-level port with no DEF binding whose same-named net '
                  'the DEF types USE POWER/GROUND in exactly one statement'),
        'disclosure': (f"{len(inferred_pg)} supply port(s) excluded as PG by a "
                       f"same-name net match, not by a DEF binding"
                       + (f": {', '.join(inferred_pg)}" if inferred_pg else "")),
    }
    return result


def _off_die(driver, inst, evidence, top_pins, masters, io_masters,
             nets_with_top_pin):
    """R-0915-128. True when the DEF says this driver faces off the die.

    TWO SHAPES, both read off the DEF:
      * the driver IS a top-level pin. The PINS section is the design's own
        statement that its driver is outside the die, so a SPEF extracted from
        the routed die cannot carry it.
      * the driver is a PAD-side terminal of an IO cell bonded to a top-level
        pin: its instance's master is in the run's LEF IO inventory AND its net
        carries a top-level PIN connection.

    WHY BOTH CONDITIONS FOR THE SECOND SHAPE. Net membership alone would also
    catch an ordinary cell driving an OUTPUT port -- and that driver is ON the
    die, so the SPEF does annotate it and excluding it would lose real evidence.
    The master test is what separates the pad from the logic, and it comes from
    the LEF inventory rather than from the master's NAME.
    """
    if driver in top_pins:
        return True
    if inst is None or not io_masters:
        return False
    if masters.get(inst) not in io_masters:
        return False
    return any(net in nets_with_top_pin for net, _use in evidence)
