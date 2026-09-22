// Grammar regression test for niko.tmLanguage.json.
//
// Run from this folder:
//   npm install --no-save vscode-textmate vscode-oniguruma && node test-grammar.js
// (The two packages are only needed for this test, not for the extension.)
const fs = require('fs');
const path = require('path');
const child_process = require('child_process');
const vsctm = require('vscode-textmate');
const onig = require('vscode-oniguruma');

const GRAMMAR_PATH = path.join(__dirname, 'syntaxes', 'niko.tmLanguage.json');

const checks = [
  ['set total: number to 0', ['number', 'storage.type.niko']],
  ['set xs: list<number> to [1]', ['list', 'storage.type.niko'], ['number', 'storage.type.niko']],
  ['say number("42")', ['number', 'support.function.niko']],
  ['match result:', ['result', 'source.niko']],
  ['if total is 3 and x is not 4:', ['is', 'keyword.operator.niko'], ['and', 'keyword.operator.niko'], ['is not', 'keyword.operator.niko']],
  ['when ok v:', ['ok', 'support.function.niko']],
  ['set r: option<result<text>> to ok("x")', ['option', 'storage.type.niko'], ['result', 'storage.type.niko'], ['text', 'storage.type.niko']],
  ['# a comment', ['# a comment', 'comment.line.number-sign.niko']],
  ['to greet with who:', ['to', 'keyword.control.niko'], ['greet', 'entity.name.function.niko'], ['with', 'variable.parameter.niko']],
  ['say "hi", who', ['hi', 'string.quoted.double.niko']],
  // single-quoted strings (the lexer accepts '...')
  ["say 'hi', who", ['hi', 'string.quoted.single.niko']],
  // list-mutation statements
  ['put 1 in xs', ['put', 'keyword.control.niko'], ['in', 'keyword.operator.niko']],
  ['remove 1 from xs', ['remove', 'keyword.control.niko'], ['from', 'keyword.control.niko']],
  ['add 1 to total', ['add', 'keyword.control.niko'], ['to', 'keyword.control.niko']],
  ['take 1 from total', ['take', 'keyword.control.niko']],
  ['ask "name?" into who', ['ask', 'keyword.control.niko'], ['into', 'keyword.control.niko']],
  // comparison operators
  ['if x is bigger than 3:', ['is bigger than', 'keyword.operator.niko']],
  ['if x is smaller than 3:', ['is smaller than', 'keyword.operator.niko']],
  ['if x is at least 3:', ['is at least', 'keyword.operator.niko']],
  ['if x is at most 3:', ['is at most', 'keyword.operator.niko']],
  ['if x is in xs:', ['is in', 'keyword.operator.niko']],
  // match as an expression, guards, patterns
  ['set r to match v:', ['match', 'keyword.control.niko']],
  ['when [first, ...rest] if ok:', ['if', 'keyword.control.niko']],
  ['when {name: n}:', ['when', 'keyword.control.niko']],
  ['when ok v:', ['ok', 'support.function.niko']],
  // modules
  ['import "lib.niko" as lib', ['import', 'keyword.control.niko'], ['as', 'keyword.control.niko'], ['lib.niko', 'string.quoted.double.niko']],
  ['use "other.niko"', ['use', 'keyword.control.niko']],
];

(async () => {
  const wasm = fs.readFileSync(path.join(__dirname, 'node_modules', 'vscode-oniguruma', 'release', 'onig.wasm'));
  await onig.loadWASM(wasm);
  const registry = new vsctm.Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (s) => new onig.OnigScanner(s),
      createOnigString: (s) => new onig.OnigString(s),
    }),
    loadGrammar: async () => vsctm.parseRawGrammar(fs.readFileSync(GRAMMAR_PATH, 'utf8'), GRAMMAR_PATH),
  });
  const grammar = await registry.loadGrammar('source.niko');
  let fail = 0;
  for (const [line, ...wants] of checks) {
    const r = grammar.tokenizeLine(line, vsctm.INITIAL);
    const toks = r.tokens.map((t) => [
      line.slice(t.startIndex, t.endIndex).trim().replace(/^[:<,]\s*/, '').replace(/\s*:\s*$/, ''),
      t.scopes.slice(-1)[0],
    ]);
    for (const [word, scope] of wants) {
      const hit = toks.find(([w]) => w === word);
      if (!hit || hit[1] !== scope) {
        fail++;
        console.log('MISMATCH', JSON.stringify(line), word, 'want', scope, 'got', hit ? hit[1] : '(missing)');
      }
    }
  }
  console.log(fail ? `FAILURES: ${fail}` : 'grammar checks passed');

  // The builtin-name list must stay in sync with the checker's canonical
  // BUILTIN_NAMES (single-quoted strings added above are lexer-legal too).
  try {
    const repoRoot = process.env.NIKO_REPO || path.join(__dirname, '..', '..');
    const names = JSON.parse(
      child_process.execSync(
        'python3 -c "from niko2.typecheck import BUILTIN_NAMES; import json; print(json.dumps(sorted(BUILTIN_NAMES)))"',
        { cwd: repoRoot, encoding: 'utf8' }
      )
    );
    const gram = JSON.parse(fs.readFileSync(GRAMMAR_PATH, 'utf8'));
    const inGrammar = gram.repository.builtin.match.replace(/^\\b\(/, '').replace(/\)\\b$/, '').split('|').sort();
    const missing = names.filter((n) => !inGrammar.includes(n));
    const extra = inGrammar.filter((n) => !names.includes(n));
    if (missing.length || extra.length) {
      fail++;
      console.log('MISMATCH builtin set. missing from grammar:', missing, 'extra in grammar:', extra);
    }
  } catch (e) {
    fail++;
    console.log('MISMATCH could not verify builtin set against niko2.typecheck:', e.message);
  }
  console.log(fail ? `FAILURES: ${fail}` : 'all grammar + builtin checks passed');
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
