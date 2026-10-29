"""Isolated regressions, without server dependencies or real outbound calls."""
import ast
import json
import re
from pathlib import Path
source = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
names = {'normalize_rsvp', 'normalize_guests', 'extract_sarvam_rsvp', 'merge_call_result'}
ns = {'json': json, 're': re}
exec(compile(ast.Module(body=[n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), '<results>', 'exec'), ns)
extract = ns['extract_sarvam_rsvp']
assert extract({'rsvp': True, 'guest_names': '["Kishore", "Vinoth"]'}) == ('Yes', ['Kishore', 'Vinoth'])
assert extract({'rsvp': {'value': 'No'}, 'guest_names': '[]'}) == ('No', [])
assert extract('{"rsvp_status":"Maybe"}')[0] == 'Maybe'
assert extract({'call_outcome': 'connected'}) == ('', [])
assert extract({'rsvp':'Unknown'}) == ('', [])
contact = {}
ns['merge_call_result'](contact, {})
assert contact['status'] == 'Call ended'
ns['merge_call_result'](contact, {'rsvp':'Yes'})
ns['merge_call_result'](contact, {'guest_names':'Kishore and Vinoth'})
assert contact['rsvp'] == 'Yes' and contact['guests'] == 'Kishore, Vinoth'
ns['merge_call_result'](contact, {})
assert contact['rsvp'] == 'Yes' and contact['guests'] == 'Kishore, Vinoth'
print('7 parsing/merge regressions passed')
