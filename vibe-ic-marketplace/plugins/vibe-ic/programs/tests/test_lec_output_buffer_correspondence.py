"""A symmetric pin swap remains visible across a proven output buffer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lec_post_layout_check as L

LIB = '''library(neutral) {
cell(logic2) {
pin(A) { direction : input; } pin(B) { direction : input; }
pin(Y) { direction : output; function : "A & B"; }
}
cell(repeater) {
pin(I) { direction : input; }
pin(Z) { direction : output; function : "I"; }
}
cell(inverter) {
pin(I) { direction : input; }
pin(Z) { direction : output; function : "!I"; }
}
}'''
GOLD='module top(input a,b, output y);\nlogic2 u (.A(a), .B(b), .Y(y));\nendmodule'
def gate(kind='repeater', extra='', pin_b='a'):
    return (f'module top(input a,b, output y);\nwire n;\n'
            f'logic2 u (.A(b), .B({pin_b}), .Y(n));\n'
            f'{kind} b0 (.I(n), .Z(y));\n{extra}\nendmodule')
def classify(text):
    return L.classify_pin_permutation_points(['u.A','u.B'], GOLD, text, LIB)

def test_output_buffer_keeps_all_pin_correspondences():
    result=classify(gate())
    assert not result['rejected']
    renames,records=L.build_pin_correspondence_renames(result['accepted'],['u.A','u.B'])
    assert renames==[('u.A','u.B'),('u.B','u.A')]
    assert len(result['accepted'][0]['output_buffer_paths']['Y'])==1

def test_output_inversion_is_not_transparent():
    assert len(classify(gate('inverter'))['rejected'])==2

def test_second_buffer_driver_refuses_correspondence():
    assert len(classify(gate(extra='repeater b1 (.I(a), .Z(y));'))['rejected'])==2

def test_second_logic_driver_refuses_correspondence():
    assert len(classify(gate(extra='logic2 u1 (.A(a), .B(b), .Y(y));'))['rejected'])==2

def test_unknown_driver_refuses_correspondence():
    assert len(classify(gate(extra='unmodelled u1 (.A(a), .Y(y));'))['rejected'])==2

def test_real_input_rewire_is_still_rejected():
    assert len(classify(gate(pin_b='b'))['rejected'])==2

def test_disconnected_output_is_still_rejected():
    assert len(classify(gate().replace('.Z(y)', '.Z(other)'))['rejected'])==2

def test_native_alias_bijection_preserves_existing_match_names():
    accepted=[{'instance':'u', 'gold_input_nets':{'A':'x','B':'y','C':'z'},
               'gate_input_nets':{'A':'y','B':'z','C':'x'}}]
    native={'gold':['u.B','u.C'], 'gate':['u.A','u.B']}
    renames,records=L.build_pin_correspondence_renames(accepted,['u.B'],native)
    assert renames==[('u.A','u.B'),('u.B','u.A')]
    assert records[0]['native_matched_pins']==['B']
    before=set(native['gate']) & set(native['gold'])
    after={dict(renames).get(n,n) for n in native['gate']} & set(native['gold'])
    assert before==after=={'u.B'}

def test_missing_native_alias_refuses_instead_of_guessing():
    accepted=[{'instance':'u', 'gold_input_nets':{'A':'x','B':'y','C':'z'},
               'gate_input_nets':{'A':'y','B':'z','C':'x'}}]
    renames,records=L.build_pin_correspondence_renames(
        accepted,['u.B'],{'gold':['u.B','u.C'],'gate':['u.B']})
    assert renames==[]
    assert 'absent' in records[0]['skipped']
