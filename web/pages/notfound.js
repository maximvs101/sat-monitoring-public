"use strict";

// Adresse inconnue : lien vers la vue d'ensemble.

const NotFoundPage = {
  mount(view) {
    view.append(el("div", { className: "page" }, ...pageHead("Page introuvable"),
      el("p", {}, "Cette adresse ne correspond à aucune page. ", el("a", { href: routeHref({ page: "overview" }), textContent: "Retour à la vue d'ensemble" }))));
  },
  unmount() {},
};
