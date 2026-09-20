import copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _documented_scalar_widths import attach_documented_scalar_widths as apply
TEXT='| Width | Name | Address |\n|---|---|---|\n| 16 | PAYLOAD | 0x40 |\n'
def row(**kw):
 return dict({'name':'PAYLOAD','address_int':64,'evidence':'input/docs/registers.md','fields':[{'field_name':'ENABLE','bits':'0'}],'access':'RW'},**kw)
def test_existing_typed_width_and_fields_preserved():
 regs=[row(width_bits=64)];before=copy.deepcopy(regs);apply(regs,{'registers.md':TEXT});assert regs==before

def test_source_binding_and_permuted_columns():
 regs=[row()];fields=copy.deepcopy(regs[0]['fields']);apply(regs,{'unrelated.md':TEXT.replace('16','32'),'registers.md':TEXT});assert regs[0]['width_bits']==16 and regs[0]['fields']==fields

def test_address_binding():
 regs=[row(address_int=65)];before=copy.deepcopy(regs);apply(regs,{'registers.md':TEXT});assert regs==before

def test_array_preserved():
 regs=[row(array=True)];before=copy.deepcopy(regs);apply(regs,{'registers.md':TEXT});assert regs==before

def test_no_unframed_or_duplicate_width_header():
 for text in ['PAYLOAD is 16 bits at 0x40', '| Address | Name | Width | Width |\n|---|---|---|---|\n| 0x40 | PAYLOAD | 16 | 32 |\n']:
  regs=[row()];before=copy.deepcopy(regs);apply(regs,{'registers.md':text});assert regs==before

# ── vibe-ic#712 polarity: a denied register map is not a declaration ────────
# Both directions on every case: the SAME table, read when its caption states
# the map and refused when the caption denies it. A one-direction test here
# would pass on an extractor that had simply stopped reading tables.

CAPTION = 'The register map for this revision:\n\n' + TEXT
DENIED = 'The register map below is superseded and does NOT apply.\n\n' + TEXT

def test_a_stating_caption_still_binds():
 regs=[row()];apply(regs,{'registers.md':CAPTION});assert regs[0]['width_bits']==16

def test_a_denying_caption_refuses_the_whole_map():
 regs=[row()];before=copy.deepcopy(regs);apply(regs,{'registers.md':DENIED});assert regs==before

def test_every_denial_word_in_the_shared_vocabulary_refuses():
 """The vocabulary is `_prose_polarity`'s, not a private list of words."""
 import _prose_polarity as pol
 for token in ('not','no','none','without','never','removed','obsolete',
               'superseded','deprecated','inapplicable','非','無'):
  text=f'This map is {token} for this part.\n\n'+TEXT
  assert pol.is_denied(text.splitlines()[0]), token
  regs=[row()];before=copy.deepcopy(regs);apply(regs,{'registers.md':text})
  assert regs==before, token

def test_the_denial_must_be_in_the_captions_own_sentence():
 """Cover stated, so nobody reads more into it than it measures.

 A denial two sentences up does NOT reach — `sentence_scope` bounds the
 window at the sentence break, and widening that here would be the private
 reach rule `_prose_polarity` exists to prevent."""
 text='This part is not a UART. The register map for this revision:\n\n'+TEXT
 regs=[row()];apply(regs,{'registers.md':text});assert regs[0]['width_bits']==16

def test_a_denial_in_a_bracketed_qualifier_does_not_retire_the_map():
 text='The register map (width not including parity) for rev 2:\n\n'+TEXT
 regs=[row()];apply(regs,{'registers.md':text});assert regs[0]['width_bits']==16

def test_an_unrelated_n_a_cell_does_not_retire_the_map():
 """`after=0`: the window never reads the table's own rows.

 `n/a` is a denial term in this vocabulary, and a map carrying one in some
 other row must still bind the rows that ARE stated."""
 text=('The register map for this revision:\n\n'
       '| Width | Name | Address |\n|---|---|---|\n'
       '| N/A | RESERVED | 0x44 |\n| 16 | PAYLOAD | 0x40 |\n')
 regs=[row()];apply(regs,{'registers.md':text});assert regs[0]['width_bits']==16

def test_two_adjacent_tables_introduce_nothing(  ):
 """A preceding TABLE LINE is not a caption, so it can neither state nor deny."""
 text=TEXT+TEXT
 regs=[row()];apply(regs,{'registers.md':text});assert regs[0]['width_bits']==16
