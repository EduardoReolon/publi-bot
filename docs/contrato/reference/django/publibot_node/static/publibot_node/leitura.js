/*
 * Medicao de leitura e conversao para o recurso `insights` do contrato PubliBot.
 *
 * Sem dependencia e sem identificar ninguem. Mede no navegador e manda para o
 * SEU servidor (nunca para o PubliBot):
 *
 *   - um resumo por abertura de artigo: tempo ATIVO (aba visivel e interacao
 *     nos ultimos 30 s), se chegou ao fim do texto, se viu e se clicou na
 *     chamada;
 *   - na conversao (clique num elemento com data-publibot-conversao), a
 *     jornada: os artigos lidos nos ultimos 30 dias, guardada no proprio
 *     navegador (localStorage) e zerada depois de converter.
 *
 * Na pagina:
 *   <article data-publibot-id="{{ remote_id }}"> ... </article>
 *   <aside data-publibot-bloco> ... <a data-publibot-conversao="whatsapp" href="..."> </aside>
 *   <script src="/static/publibot_node/leitura.js" data-endpoint="/api/v1/" defer></script>
 *
 * Para respeitar um banner de consentimento: window.publibotMedir = false
 * antes de carregar o script.
 */
(function () {
  "use strict";
  if (window.publibotMedir === false) return;

  var script = document.currentScript;
  var base = (script && script.getAttribute("data-endpoint")) || "/api/v1/";
  var CHAVE = "publibot:jornada";
  var DIAS = 30;
  var INATIVO_APOS = 30000;
  var TETO = 1800;

  function enviar(rota, dados) {
    var corpo = JSON.stringify(dados);
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(base + rota, new Blob([corpo], { type: "application/json" }));
        return;
      }
    } catch (e) { /* cai no fetch */ }
    try {
      fetch(base + rota, { method: "POST", body: corpo, keepalive: true,
        headers: { "Content-Type": "application/json" } });
    } catch (e) { /* medicao nunca quebra a pagina */ }
  }

  function lerJornada() {
    try {
      var limite = Date.now() - DIAS * 86400000;
      return (JSON.parse(localStorage.getItem(CHAVE)) || []).filter(function (item) {
        return item && item.id && item.d > limite;
      });
    } catch (e) { return []; }
  }

  function gravarJornada(lista) {
    try { localStorage.setItem(CHAVE, JSON.stringify(lista.slice(-20))); } catch (e) { /* sem espaco ou bloqueado */ }
  }

  function acrescentar(lista, id, segundos) {
    var anterior = 0;
    lista = lista.filter(function (item) {
      if (item.id === id) { anterior = item.s || 0; return false; }
      return true;
    });
    lista.push({ id: id, s: anterior + segundos, d: Date.now() });
    return lista;
  }

  function novoId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx".replace(/x/g, function () {
      return (Math.random() * 16 | 0).toString(16);
    });
  }

  var artigo = document.querySelector("[data-publibot-id]");
  var id = artigo ? artigo.getAttribute("data-publibot-id") : null;
  var ativo = 0, ultimaInteracao = Date.now();
  var chegouAoFim = false, viuChamada = false, clicouChamada = false, enviado = false, converteu = false;

  // Conversao: vale em qualquer pagina do site, inclusive a landing page.
  document.addEventListener("click", function (evento) {
    var alvo = evento.target.closest && evento.target.closest("[data-publibot-conversao]");
    var bloco = evento.target.closest && evento.target.closest("[data-publibot-bloco]");
    if (bloco && id) clicouChamada = true;
    if (!alvo) return;
    var jornada = lerJornada();
    if (id) jornada = acrescentar(jornada, id, ativo);
    enviar("conversao/", {
      id: novoId(),
      kind: alvo.getAttribute("data-publibot-conversao") || "",
      via_cta: !!(bloco && id),
      journey: jornada.map(function (item) { return { id: item.id, s: item.s }; })
    });
    gravarJornada([]);
    converteu = true;
  }, true);

  if (!id) return;

  ["scroll", "pointerdown", "pointermove", "keydown", "touchstart", "wheel"].forEach(function (nome) {
    window.addEventListener(nome, function () { ultimaInteracao = Date.now(); }, { passive: true });
  });

  setInterval(function () {
    if (document.visibilityState === "visible" && Date.now() - ultimaInteracao < INATIVO_APOS && ativo < TETO) {
      ativo += 1;
    }
  }, 1000);

  if ("IntersectionObserver" in window) {
    var fim = document.createElement("div");
    fim.setAttribute("aria-hidden", "true");
    artigo.appendChild(fim);
    new IntersectionObserver(function (itens, observador) {
      if (itens.some(function (i) { return i.isIntersecting; })) {
        chegouAoFim = true; observador.disconnect();
      }
    }).observe(fim);

    var blocos = document.querySelectorAll("[data-publibot-bloco]");
    if (blocos.length) {
      var olhoNaChamada = new IntersectionObserver(function (itens) {
        if (itens.some(function (i) { return i.isIntersecting; })) {
          viuChamada = true; olhoNaChamada.disconnect();
        }
      }, { threshold: 0.5 });
      blocos.forEach(function (b) { olhoNaChamada.observe(b); });
    }
  }

  // Um resumo por abertura, na primeira vez que a pagina sai de vista (troca
  // de aba, app em segundo plano, fechar). E o ultimo momento confiavel em
  // celular; o que for lido depois de voltar a aba nao e contado.
  function concluir() {
    if (enviado) return;
    enviado = true;
    enviar("leitura/", { id: id, active: ativo, end: chegouAoFim, cta_seen: viuChamada, cta_click: clicouChamada });
    // Depois de converter, a jornada recomeca vazia: este artigo ja contou.
    if (!converteu) gravarJornada(acrescentar(lerJornada(), id, ativo));
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") concluir();
  });
  window.addEventListener("pagehide", concluir);
})();
