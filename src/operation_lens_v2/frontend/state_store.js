(function () {
  function createStore(namespace) {
    const prefix = `${namespace}:`;

    function get(key, fallback = null) {
      try {
        const raw = sessionStorage.getItem(prefix + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (_) {
        return fallback;
      }
    }

    function set(key, value) {
      try {
        sessionStorage.setItem(prefix + key, JSON.stringify(value));
      } catch (_) {}
    }

    function subscribe(eventName, callback) {
      window.addEventListener(eventName, (event) => callback(event?.detail));
    }

    function clearByPrefix(keyPrefix) {
      const full = prefix + keyPrefix;
      Object.keys(sessionStorage).forEach((key) => {
        if (key.startsWith(full)) sessionStorage.removeItem(key);
      });
    }

    return { get, set, subscribe, clearByPrefix };
  }

  window.LensStateStore = { createStore };
})();
