/* Widget de chat embeddable NovaMart Support.
 * Usage : <script src="widget.js" defer></script>
 * Config optionnelle : window.NOVAMART_CHAT_API = "http://localhost:8000";
 */
(function () {
  "use strict";

  var API_BASE = window.NOVAMART_CHAT_API || "http://localhost:8000";
  var sessionId = "";

  function el(tag, attrs, html) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  function build() {
    var root = el("div", { id: "novamart-widget" });

    var button = el("button", { id: "novamart-widget-button", "aria-label": "Ouvrir le chat" }, "💬");
    var panel = el("div", { id: "novamart-widget-panel" });

    var header = el("div", { class: "novamart-header" }, "NovaMart Support");
    var messages = el("div", { class: "novamart-messages", id: "novamart-messages" });

    var inputWrap = el("div", { class: "novamart-input" });
    var input = el("input", { type: "text", placeholder: "Votre message…", id: "novamart-input" });
    var send = el("button", { type: "button" }, "Envoyer");
    inputWrap.appendChild(input);
    inputWrap.appendChild(send);

    panel.appendChild(header);
    panel.appendChild(messages);
    panel.appendChild(inputWrap);

    root.appendChild(button);
    root.appendChild(panel);
    document.body.appendChild(root);

    button.addEventListener("click", function () {
      panel.classList.toggle("open");
      if (panel.classList.contains("open")) input.focus();
    });

    function addMessage(text, who) {
      var m = el("div", { class: "novamart-msg " + who }, "");
      m.innerHTML = who === "bot" ? marked.parse(text) : "";
      if (who !== "bot") m.textContent = text;
      messages.appendChild(m);
      messages.scrollTop = messages.scrollHeight;
      return m;
    }

    function submit() {
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      addMessage(text, "user");
      var pending = addMessage("…", "bot");

      fetch(API_BASE + "/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId })
      })
        .then(function (response) {
          if (!response.ok || !response.body) {
            throw new Error("stream unavailable");
          }

          var reader = response.body.getReader();
          var decoder = new TextDecoder();
          var buffer = "";   // octets SSE pas encore decoupes en evenements
          var answer = "";   // texte accumule de la reponse

          function render() {
            pending.innerHTML = marked.parse(answer || "…");
            messages.scrollTop = messages.scrollHeight;
          }

          function pump() {
            return reader.read().then(function (result) {
              if (result.done) {
                render();
                return;
              }

              buffer += decoder.decode(result.value, { stream: true });

              // Les evenements SSE sont separes par "\n\n".
              var events = buffer.split("\n\n");
              buffer = events.pop();   // reste partiel garde pour le prochain chunk

              events.forEach(function (evt) {
                if (evt.indexOf("data:") === 0) {
                  answer += JSON.parse(evt.slice(5));
                }
              });

              render();
              return pump();
            });
          }

          return pump();
        })
        .catch(function () {
          pending.textContent = "Erreur de connexion au serveur.";
        });
    }

    send.addEventListener("click", submit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") submit();
    });

    addMessage("Bonjour ! Comment puis-je vous aider aujourd'hui ?", "bot");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
