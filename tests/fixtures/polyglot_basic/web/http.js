/* Shared HTTP helpers (script, no modules). */
(function (global) {
  "use strict";
  var RE = /}{/g;  // a regex with braces
  function request(url, opts) {
    if (!url) { return null; }
    return fetch(url, opts).then(function (r) { return r.json(); });
  }
  function _private() {}
  global.WebHttp = { request: request, version: 2 };
})(window);
