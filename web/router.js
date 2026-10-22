"use strict";

// Navigation par l'adresse (#/…) : à chaque changement, la page quittée est démontée (graphes détruits,
// abonnements annulés) et la nouvelle montée. Chargé en dernier : démarre l'application.

const Router = (() => {
  const PAGES = { overview: OverviewPage, carrier: CarrierPage, journal: JournalPage, fsh: FshPage,
    settings: SettingsPage, notfound: NotFoundPage };
  let current = null;

  function go() {
    const route = parseRoute(location.hash);
    if (current) {
      try { current.unmount(); } catch (e) { console.error(e); }
    }
    const view = $("view");
    view.replaceChildren();
    window.scrollTo(0, 0);
    current = PAGES[route.page];
    Nav.setRoute(route);
    try {
      current.mount(view, route);
    } catch (e) {
      console.error(e);
      view.replaceChildren(el("div", { className: "page" }, el("p", { className: "error", textContent: `Erreur d'affichage : ${e.message}` })));
    }
  }

  async function start() {
    Nav.init($("nav"));
    try {
      await Store.start();
    } catch (e) {
      console.error(e);
    }
    window.addEventListener("hashchange", go);
    go();
  }

  return { start, go };
})();

Router.start();
