/* NovaMart Support — embeddable chat widget.
 * Usage : <script src="widget.js" defer></script>
 * Config : window.NOVAMART_CHAT_API = "https://...";
 *
 * Features : SSE streaming (/chat/stream), Markdown rendering (marked.js),
 * session_id, suggested-question chips, typing indicator, bot avatars.
 * Widget starts CLOSED ; chips appear on the first FAB click.
 */
(function () {
  "use strict";

  var API_BASE = window.NOVAMART_CHAT_API || "http://localhost:8000";
  var BOT = "🤖";

  var SUGGESTIONS = [
    { label: "📦 Délais de livraison ?", query: "Quels sont vos délais de livraison en France ?" },
    { label: "↩️ Faire un retour ?", query: "Comment faire un retour ?" },
    { label: "🛍️ Voir le catalogue ?", query: "Que proposez-vous au catalogue ?" }
  ];

  var sessionId = "";
  var chipsRendered = false;
  var els = {};

  /* -------------------------------------------------------------- helpers */

  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function scrollDown() {
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  /* ---------------------------------------------------------------- build */

  function build() {
    var fab = h("button", { id: "novamart-fab", "aria-label": "Ouvrir le chat", text: "💬" });

    var closeBtn = h("button", { class: "nm-close", "aria-label": "Fermer", text: "✕" });
    var header = h("div", { class: "nm-header" }, [
      h("div", { class: "nm-avatar", text: BOT }),
      h("div", { class: "nm-header-text" }, [
        h("span", { class: "nm-title", text: "NovaMart Assistant" }),
        h("span", { class: "nm-status", text: "● En ligne" })
      ]),
      closeBtn
    ]);

    var messages = h("div", { class: "nm-messages" });

    var input = h("input", {
      class: "nm-input",
      type: "text",
      placeholder: "Écrivez votre message…"
    });
    var send = h("button", { class: "nm-send", "aria-label": "Envoyer", text: "➤" });
    var inputBar = h("div", { class: "nm-input-bar" }, [input, send]);

    var panel = h("div", { id: "novamart-panel" }, [header, messages, inputBar]);
    document.body.appendChild(h("div", { id: "novamart-widget" }, [fab, panel]));

    els = { fab: fab, panel: panel, messages: messages, input: input, send: send };

    fab.addEventListener("click", togglePanel);
    closeBtn.addEventListener("click", closePanel);
    send.addEventListener("click", function () { submit(input.value); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") submit(input.value);
    });

    addBotMessage("Bonjour 👋 Je suis l'assistant NovaMart. Comment puis-je vous aider ?");
  }

  function togglePanel() {
    els.panel.classList.toggle("open");
    if (els.panel.classList.contains("open")) {
      els.input.focus();
      if (!chipsRendered) {
        renderChips();
        chipsRendered = true;
      }
    }
  }

  function closePanel() {
    els.panel.classList.remove("open");
  }

  /* ------------------------------------------------------------- messages */

  function addUserMessage(text) {
    var bubble = h("div", { class: "nm-bubble" });
    bubble.textContent = text;
    els.messages.appendChild(h("div", { class: "nm-msg user" }, [bubble]));
    scrollDown();
  }

  function addBotRow(bubble) {
    var row = h("div", { class: "nm-msg bot" }, [
      h("div", { class: "nm-msg-avatar", text: BOT }),
      bubble
    ]);
    els.messages.appendChild(row);
    scrollDown();
  }

  function addBotMessage(markdown) {
    var bubble = h("div", { class: "nm-bubble" });
    bubble.innerHTML = marked.parse(markdown);
    addBotRow(bubble);
    return bubble;
  }

  function addTypingBubble() {
    var bubble = h("div", { class: "nm-bubble nm-bubble-typing" }, [
      h("div", { class: "nm-typing" }, [
        h("span", { class: "nm-dot" }),
        h("span", { class: "nm-dot" }),
        h("span", { class: "nm-dot" })
      ])
    ]);
    addBotRow(bubble);
    return bubble;
  }

  /* ---------------------------------------------------------------- chips */

  function renderChips() {
    var wrap = h("div", { class: "nm-chips", id: "nm-chips" });
    SUGGESTIONS.forEach(function (s) {
      var chip = h("button", { class: "nm-chip", text: s.label });
      chip.addEventListener("click", function () { submit(s.query); });
      wrap.appendChild(chip);
    });
    els.messages.appendChild(wrap);
    scrollDown();
  }

  function removeChips() {
    var chips = document.getElementById("nm-chips");
    if (chips) chips.remove();
  }

  /* --------------------------------------------------------------- submit */

  function submit(raw) {
    var text = (raw || "").trim();
    if (!text) return;

    els.input.value = "";
    removeChips();
    addUserMessage(text);

    var bubble = addTypingBubble();
    var started = false;
    var answer = "";

    function render() {
      bubble.innerHTML = marked.parse(answer || "…");
      scrollDown();
    }

    fetch(API_BASE + "/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId })
    })
      .then(function (response) {
        if (!response.ok || !response.body) throw new Error("stream unavailable");

        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";

        function pump() {
          return reader.read().then(function (result) {
            if (result.done) {
              bubble.classList.remove("nm-bubble-typing");
              render();
              return;
            }

            buffer += decoder.decode(result.value, { stream: true });

            // Evenements SSE separes par "\n\n" ; chaque "data:" est du JSON.
            var events = buffer.split("\n\n");
            buffer = events.pop();

            events.forEach(function (evt) {
              if (evt.indexOf("data:") !== 0) return;
              var chunk;
              try { chunk = JSON.parse(evt.slice(5)); } catch (e) { return; }

              if (!started) {
                started = true;
                bubble.classList.remove("nm-bubble-typing");
                bubble.innerHTML = "";
              }
              answer += chunk;
            });

            if (started) render();
            return pump();
          });
        }

        return pump();
      })
      .catch(function () {
        bubble.classList.remove("nm-bubble-typing");
        bubble.textContent = "Erreur de connexion au serveur. Réessayez.";
      });
  }

  /* ----------------------------------------------------------------- init */

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
