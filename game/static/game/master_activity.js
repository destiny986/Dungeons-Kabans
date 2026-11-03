/* Background notifications belong to this master tab, never to a player grant. */
(() => {
  'use strict';
  const config = document.querySelector('[data-activity-url]');
  if (!config) return;
  const originalTitle = document.title;
  const kinds = new Set(['text', 'action_use', 'item_remove']);
  let seen = 0n, generation = 0, pollTimer, blinkTimer, controller;
  let count = 0, alertPhase = true;

  function acknowledgeRendered() {
    for (const message of document.querySelectorAll('#master-messages .chat-message')) {
      if (message.classList.contains('from-master') || !kinds.has(message.dataset.messageKind)) continue;
      const id = BigInt(message.dataset.messageId);
      if (id > seen) seen = id;
    }
  }

  function paintTitle() {
    document.title = count ? (alertPhase ? `🔴 Новых сообщений: ${count}` : `(${count}) ${originalTitle}`) : originalTitle;
  }

  function blink() {
    alertPhase = !alertPhase;
    paintTitle();
    blinkTimer = setTimeout(blink, 1000);
  }

  function updateCount(value) {
    const wasEmpty = count === 0;
    count = value;
    if (!count) {
      clearTimeout(blinkTimer); blinkTimer = undefined;
      alertPhase = true;
    } else if (wasEmpty) {
      alertPhase = true;
      blinkTimer = setTimeout(blink, 1000);
    }
    paintTitle();
  }

  async function poll(epoch) {
    const request = new AbortController();
    controller = request;
    const timeout = setTimeout(() => request.abort(), 15000);
    try {
      const query = new URLSearchParams({after:seen.toString()});
      const response = await fetch(`${config.dataset.activityUrl}?${query}`, {
        credentials:'same-origin', cache:'no-store', signal:request.signal,
      });
      if (!response.ok) throw new Error('Activity unavailable');
      const activity = await response.json();
      if (!Number.isSafeInteger(activity.count) || activity.count < 0 ||
          typeof activity.max_id !== 'string' || !/^\d+$/.test(activity.max_id)) throw new Error('Invalid activity');
      if (epoch === generation && document.hidden) updateCount(activity.count);
    } catch (_) {
      // Keep the last successful count; retry without touching chat drafts.
    } finally {
      clearTimeout(timeout);
      if (controller === request) controller = undefined;
      if (epoch === generation && document.hidden) pollTimer = setTimeout(() => poll(epoch), 5000);
    }
  }

  function visibilityChanged() {
    generation++;
    clearTimeout(pollTimer);
    controller?.abort();
    updateCount(0);
    if (document.hidden) poll(generation);
    else acknowledgeRendered();
    // app.js already refreshes HTMX panels on visibility return.
  }

  acknowledgeRendered(); // Initial history is the baseline, even in a background tab.
  document.addEventListener('visibilitychange', visibilityChanged);
  document.addEventListener('htmx:beforeRequest', event => {
    // HTMX can queue a visible refresh and send it after the page hides.
    if (document.hidden && event.detail.elt?.id === 'poller') event.preventDefault();
  });
  document.addEventListener('htmx:oobAfterSwap', event => {
    if (!document.hidden && event.detail.target?.id === 'master-messages') acknowledgeRendered();
  });
  if (document.hidden) poll(generation);
})();
