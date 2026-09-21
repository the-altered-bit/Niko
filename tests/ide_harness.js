// Runs one .niko file through the browser IDE's translator (no browser needed).
// Usage: node ide_harness.js file.niko [input line ...]
const fs = require('fs'), path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'ide', 'niko-ide.html'), 'utf8');
const region = html.match(/\/\*<TRANSPILER>\*\/([\s\S]*?)\/\*<\/TRANSPILER>\*\//)[1];
const { diagnose, ENV, LIB, fmt, friendly, AsyncFunction } =
  new Function(region + '\nreturn {diagnose, ENV, LIB, fmt, friendly, AsyncFunction};')();
const [, , file, ...inputs] = process.argv;
(async () => {
  const d = diagnose(fs.readFileSync(file, 'utf8'));
  if (d.problems.length) { const p = d.problems[0], src = fs.readFileSync(file, 'utf8').split('\n'); console.log('Oops - line ' + p.line + ': ' + p.msg + '  ->  ' + (src[p.line - 1] || '').trim()); return; }
  let buf = ''; const st = { l: 0 };
  const ask = async p => { buf += fmt(p); return inputs.shift(); };
  const env = Object.assign({}, LIB, {
    say: (...a) => { buf += a.map(x => fmt(x)).join(' ') + '\n'; },
    ask,
    askNumber: async p => { for (;;) { const t = String(await ask(p)).trim(), n = Number(t); if (t !== '' && !isNaN(n)) return n; buf += 'Please type a number.\n'; } },
    __tick: async () => {}, __st: st, sleep: async () => {}
  });
  try { await new AsyncFunction(...ENV, '"use strict";\n' + d.t.lines.join('\n') + d.t.tail)(...ENV.map(k => env[k])); }
  catch (e) { buf += 'Oops on line ' + st.l + ': ' + friendly(e, d.t.names) + '\n'; }
  process.stdout.write(buf);
})();
