// Canal de entrada do leitura.js (anuncio, organico, social...), com entradas simuladas.
const fs = require('fs');
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "docs/contrato/reference/django/publibot_node/static/publibot_node/leitura.js"), "utf8");
const corpo = src.slice(src.indexOf('function canalDaEntrada()'), src.indexOf('function lerCanais()'));
function caso(url, ref) {
  const u = new URL(url);
  const location = { search: u.search, hostname: u.hostname };
  const document = { referrer: ref };
  return new Function('location', 'document', corpo + '; return canalDaEntrada();')(location, document);
}
const casos = [
  ['https://site.com.br/lp?gclid=abc', 'https://www.google.com/', 'paid'],
  ['https://site.com.br/lp?utm_medium=cpc', '', 'paid'],
  ['https://site.com.br/artigo', 'https://www.google.com/', 'organic'],
  ['https://site.com.br/artigo', 'https://search.brave.com/search?q=x', 'organic'],
  ['https://site.com.br/artigo', 'https://l.instagram.com/', 'social'],
  ['https://site.com.br/artigo', 'https://t.co/abc', 'social'],
  ['https://site.com.br/artigo', '', 'direct'],
  ['https://site.com.br/artigo', 'https://site.com.br/outro', null],
  ['https://site.com.br/artigo', 'https://blog.parceiro.com/', 'referral'],
  ['https://site.com.br/artigo?utm_medium=email', '', 'email'],
];
let falhas = 0;
for (const [url, ref, esperado] of casos) {
  const obtido = caso(url, ref);
  if (obtido !== esperado) { falhas++; console.log('FALHOU', url, ref, obtido, esperado); }
}
console.log(falhas ? `${falhas} falha(s)` : `todos os ${casos.length} casos ok`);
process.exit(falhas ? 1 : 0);
