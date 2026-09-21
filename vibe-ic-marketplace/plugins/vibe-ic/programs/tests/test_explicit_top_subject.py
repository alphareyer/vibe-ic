from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import phase1_doc_one_shot_runner as D

@pytest.mark.parametrize('text,expected',[
 ('`sha256` 為**單一 top module**,內部包含 register file + hash datapath(具體拆分由 Plugin 自選)。','sha256'),
 ('`packet_unit` 是**單一 top module**。','packet_unit'),
 ('`packet_unit` is the single top module.','packet_unit'),
 ('`packet_unit` is not the single top module.',None),
 ('Previously `old_unit` was the single top module.',None),
 ('The design has no specified top.',None),
 ('`alpha_unit` 為**單一 top module**。\n`beta_unit` 為**單一 top module**。',None),
 ('`alpha_unit` 為**單一 top module**。\n`alpha_unit` 為**單一 top module**。','alpha_unit'),
],ids=['original','opposite_name','english','negated','historical','absent','ambiguous','repeated_same'])
def test_subject(text,expected):
 assert D._extract_top_module_from_docs({'input.md':text})==expected

# vibe-ic#731 — the convention reads a SENTENCE, so it must not read a
# commented-out one. MEASURED before the strip landed: the BLOCK-comment case
# below returned `foo_top`, i.e. an author SHOWING code was read as this design
# declaring its top. The `//` case was already safe by the anchor (`^\s*` then
# the quoted subject, which `//` precedes) and is pinned beside it so the two
# reasons are not confused with one.
@pytest.mark.parametrize('text,expected',[
 ('/*\n`foo_top` is the single top module.\n*/','block_commented_out'),
 ('// `foo_top` is the single top module.','line_commented_out'),
 ('`foo_top` is the single top module.','real'),
],ids=['block_comment','line_comment','uncommented_control'])
def test_a_commented_out_declaration_is_not_this_designs_top(text,expected):
 got=D._extract_top_module_from_docs({'input.md':text})
 assert got==(None if expected!='real' else 'foo_top'), (expected,got)


def test_full_minimal_l9_producer(tmp_path):
 fixture=Path(__file__).resolve().parent/'fixtures/phase1_local/explicit_top_context_local/L8_submodule_integration.md'
 docs={fixture.name:fixture.read_text()}
 inp=tmp_path/'input/docs';inp.mkdir(parents=True);(inp/fixture.name).write_text(docs[fixture.name])
 import json
 out=D.gen_l9_integration_spec(tmp_path,docs,{})
 data=json.loads(out.path.read_text())
 assert data['top_module']=='sha256'
 assert data['top_module_extraction_strategy']=='doc_module_decl_or_heading'
 assert not data.get('no_top_module_in_input',False)
