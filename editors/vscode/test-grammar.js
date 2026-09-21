// Grammar regression test for niko.tmLanguage.json.
//
// Run from this folder:
//   npm install --no-save vscode-textmate vscode-oniguruma && node test-grammar.js
// (The two packages are only needed for this test, not for the extension.)
const fs = require('fs');
const path = require('path');
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
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
