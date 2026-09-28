// Photo admin entry. The admin shell (layouts/Admin.astro) signs the owner in;
// this waits for it, then loads the library and the slots.
import { isMock } from '../admin-core/api';
import { ready } from '../admin-core/session';
import { toast } from '../admin-core/ui';
import { realApi } from './api';
import { $ } from './dom';
import { initLibrary, reloadMedia } from './library';
import { mockApi } from './mock';
import { initSlots } from './slots';
import { dirtyKeys, setSlots, state } from './state';

state.api = isMock() ? mockApi : realApi;

// Placed-but-unsaved photos live only on this page: ask before leaving it.
window.addEventListener('beforeunload', (e) => {
  if (dirtyKeys().length) {
    e.preventDefault();
    e.returnValue = '';
  }
});

// A 401 mid-session (password changed, session expired): keep the drafts on this
// page and let the owner sign in again in another tab, then press Save here.
let warned = false;
window.addEventListener('adm:unauth', () => {
  if (warned) return;
  warned = true;
  toast('You were signed out. Sign in again in a new tab, then save here: your changes are kept.', {
    tone: 'bad',
    timeout: 0,
    action: {
      label: 'Sign in',
      run: () => {
        warned = false;
        window.open('/admin', '_blank', 'noopener');
      },
    },
  });
});

async function boot() {
  await ready();
  initLibrary();
  initSlots();
  await load();
}

async function load() {
  const loading = $('[data-loading]');
  loading.textContent = 'Loading the photos…';
  try {
    // Library first: the slot drafts compare against each photo's own description.
    await reloadMedia();
    setSlots(await state.api.slots());
  } catch (e) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'adm-btn adm-btn--secondary adm-btn--sm';
    retry.textContent = 'Try again';
    retry.addEventListener('click', () => void load());
    loading.replaceChildren(`The photos didn’t load. ${(e as Error).message} `, retry);
    retry.focus();
    return;
  }
  loading.hidden = true;
  $('[data-app]').hidden = false;
}

void boot();
