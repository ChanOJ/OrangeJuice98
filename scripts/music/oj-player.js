// Orange Juice menu jukebox: the two modules the old Modplug plug-in played (ojtheme, pressme).
// Nothing plays until the visitor clicks (the original had autostart=false); the second track follows the first.
// Uses chiptune3 (libopenmpt, see LICENSE.txt) from this folder; no network access beyond the site itself.
import { ChiptuneJsPlayer } from './chiptune3.js';

const TRACKS = [
  { url: '/music/ojtheme.xm', name: 'Orange Juice theme' },
  { url: '/music/pressme.xm', name: 'Press me' },
];
const box = document.getElementById('oj-player');

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) { /* storage blocked: fine */ }
  return null;
}

if (box && window.AudioContext && window.AudioWorklet) {
  const play = box.querySelector('[data-act=play]');
  const next = box.querySelector('[data-act=next]');
  const vol = box.querySelector('[data-act=vol]');
  const label = box.querySelector('[data-act=name]');
  let player = null, ready = false, index = 0, current = -1, state = 'stopped'; // stopped | playing | paused

  const paint = () => {
    play.textContent = state === 'playing' ? '❚❚' : '▶';
    play.setAttribute('aria-label', state === 'playing' ? 'Pause' : 'Play');
    label.textContent = TRACKS[index].name;
    box.dataset.state = state;
  };
  const setVolume = () => {
    if (player) player.gain.gain.value = vol.value / 100;
    store('oj-music-volume', vol.value);
  };
  const load = () => { player.load(TRACKS[index].url); current = index; state = 'playing'; paint(); };
  const start = () => {
    if (!player) {
      player = new ChiptuneJsPlayer({ repeatCount: 0 });
      player.onInitialized(() => { ready = true; setVolume(); load(); });
      player.onEnded(() => { index = (index + 1) % TRACKS.length; load(); });
      player.onError(() => { state = 'stopped'; label.textContent = 'cannot play'; box.dataset.state = state; });
      state = 'playing';
      paint();
    } else if (ready && state === 'paused' && current === index) {
      player.context.resume();
      player.unpause();
      state = 'playing';
      paint();
    } else if (ready) {
      load();
    }
  };

  play.addEventListener('click', () => {
    if (state === 'playing') { player.pause(); state = 'paused'; paint(); } else start();
  });
  next.addEventListener('click', () => {
    index = (index + 1) % TRACKS.length;
    if (state === 'playing') load(); else paint();
  });
  vol.addEventListener('input', setVolume);

  const saved = parseInt(store('oj-music-volume'), 10);
  if (saved >= 0 && saved <= 100) vol.value = saved;
  paint();
  box.hidden = false;
}
