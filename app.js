'use strict';
const $ = id => document.getElementById(id);
let state = null, pending = false, stopped = false, inventoryKey = '', localError = '';
$('redirect').value = location.origin + '/callback';

async function post(path, body = {}) {
  const response = await fetch('/api/' + path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function showError(message) { $('error').textContent = message; $('error').hidden = !message; }
function controls() {
  if (stopped) { document.querySelectorAll('button,input').forEach(el => {el.disabled = true;}); return; }
  const busy = pending || !state || state.busy;
  for (const id of ['save', 'disconnect', 'shutdown', 'client', 'public']) $(id).disabled = busy;
  for (const role of ['source', 'target']) $('connect-' + role).disabled = busy || !state.client_id;
  $('export').disabled = busy || !state.accounts.source || !!state.inventory;
  $('transfer').disabled = busy || !state.accounts.source || !state.accounts.target || !state.inventory || $('confirm').value.trim() !== state.accounts.target.id;
  $('pause').hidden = !state || !state.busy;
}
function renderInventory(inv) {
  const key = JSON.stringify(inv);
  if (key === inventoryKey) return;
  inventoryKey = key;
  $('inventory').hidden = !inv; $('empty').hidden = !!inv; $('download').hidden = !inv;
  $('counts').replaceChildren(); $('playlists').replaceChildren(); $('warnings').replaceChildren();
  if (!inv) return;
  const labels = {tracks:'Liked songs',albums:'Albums',shows:'Shows',episodes:'Episodes',audiobooks:'Audiobooks',artists:'Artists'};
  for (const [kind, count] of [...Object.entries(inv.library), ['playlists', inv.playlists.length]]) {
    const div = document.createElement('div'); div.className = 'metric';
    const strong = document.createElement('strong'); strong.textContent = count.toLocaleString();
    const span = document.createElement('span'); span.textContent = labels[kind] || 'Playlists';
    div.append(strong, span); $('counts').append(div);
  }
  for (const p of inv.playlists) {
    const row = document.createElement('tr');
    for (const text of [p.name, {copy:'Copy',follow:'Follow',unavailable:'Unavailable'}[p.action], p.action === 'copy' ? p.items : '-']) {
      const td = document.createElement('td'); td.textContent = text; row.append(td);
    }
    $('playlists').append(row);
  }
  for (const warning of inv.warnings) { const li = document.createElement('li'); li.textContent = warning; $('warnings').append(li); }
}
async function refresh() {
  if (stopped) return;
  const response = await fetch('/api/status');
  if (!response.ok) throw new Error('Local session expired. Reload this page.');
  state = await response.json();
  if (document.activeElement !== $('client')) $('client').value = state.client_id;
  for (const role of ['source', 'target']) {
    const account = state.accounts[role];
    $(role + '-name').textContent = account ? account.name : 'Not connected';
    $(role + '-id').textContent = account ? account.id : '\u00a0';
    $('connect-' + role).textContent = account ? 'Change account' : 'Connect ' + (role === 'source' ? 'old' : 'new') + ' account';
  }
  $('confirm').placeholder = state.accounts.target ? state.accounts.target.id : 'Connect the new account first';
  $('message').textContent = state.message; showError(localError || state.error);
  renderInventory(state.inventory);
  $('result').replaceChildren(); $('result').hidden = !state.result;
  if (state.result) {
    const summary = document.createElement('p'); summary.textContent = `${state.result.playlists} playlist copies checked; ${state.result.library_added} library items added.`;
    const list = document.createElement('ul');
    for (const issue of state.result.issues) { const li = document.createElement('li'); li.textContent = issue; list.append(li); }
    $('result').append(summary, list);
  }
  controls();
}
function action(id, task) {
  $(id).addEventListener('click', async () => {
    if (pending) return;
    pending = true; localError = ''; controls();
    try { await task(); if (!stopped) await refresh(); }
    catch (error) { localError = error.message; showError(localError); }
    finally { pending = false; controls(); }
  });
}
action('save', () => post('config', {client_id: $('client').value.trim()}));
for (const role of ['source', 'target']) action('connect-' + role, async () => { const result = await post('connect', {role}); location.assign(result.url); });
action('disconnect', () => post('disconnect'));
action('export', () => post('export'));
action('transfer', () => post('transfer', {confirm_target: $('confirm').value.trim(), preserve_public: $('public').checked}));
action('pause', () => post('cancel'));
action('shutdown', async () => { await post('shutdown'); stopped = true; $('message').textContent = 'Local service stopped. Account tokens cleared.'; document.querySelectorAll('button,input').forEach(el => {el.disabled = true;}); });
$('confirm').addEventListener('input', controls);
async function poll() {
  try { await refresh(); } catch (error) { showError(error.message); }
  if (!stopped) setTimeout(poll, 2000);
}
poll();
