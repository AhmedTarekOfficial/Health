import { createStarfield } from './starfield.js';
import {
  icon
} from '../../shared/icons.js';
export function initAmbient() {
  const stars = createStarfield(document.querySelector('#starfield'));
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let paused = reducedMotion.matches;
  const motion=document.querySelector('#motion-toggle');
  function syncMotion() {

    document.body.classList.toggle('motion-paused', paused || document.hidden);
    motion.setAttribute('aria-pressed',String(paused));
    motion.setAttribute('aria-label',paused?'Resume background motion':'Pause background motion');
    motion.title=motion.getAttribute('aria-label');
    motion.innerHTML=icon(paused?'play':'pause');
    stars.setRunning(!paused && !document.hidden);
  }
  motion.addEventListener('click',()=> {
    paused=!paused;
    syncMotion();
  });
  reducedMotion.addEventListener('change',e=> {
    paused=e.matches;
    syncMotion();
  });
  document.addEventListener('visibilitychange',syncMotion);
  syncMotion();
  // Cabin-like filtered noise, generated only after an explicit sound toggle.
  // No autoplay, music, network audio, or microphone access.
  let audio=null,source=null,enabled=false;
  const sound=document.querySelector('#sound-toggle');
  sound.addEventListener('click',async()=> {
    try {
      if(!audio) {
        const Audio=window.AudioContext||window.webkitAudioContext;
        if(!Audio)throw new Error('unsupported');
        audio=new Audio();
        const buffer=audio.createBuffer(1,audio.sampleRate*4,audio.sampleRate),data=buffer.getChannelData(0);
        for(let i=0;
        i<data.length;
        i++)data[i]=(Math.random()*2-1);
        source=audio.createBufferSource();
        source.buffer=buffer;
        source.loop=true;
        const filter=audio.createBiquadFilter();
        filter.type='lowpass';
        filter.frequency.value=190;
        const gain=audio.createGain();
        gain.gain.value=.055;
        source.connect(filter).connect(gain).connect(audio.destination);
        source.start();
      }
      enabled=!enabled;
      if(enabled)await audio.resume();
      else await audio.suspend();
      sound.setAttribute('aria-pressed',String(enabled));
      sound.innerHTML=`${icon(enabled?'soundOn':'soundOff')}<span>${enabled?'Sound on':'Sound off'}</span>`;
    }
    catch {
      sound.textContent='Sound unavailable';
      sound.disabled=true;
    }
  });
  document.addEventListener('visibilitychange',()=> {
    if(audio) {
      if(document.hidden)audio.suspend();
      else if(enabled)audio.resume();
    }
  });
}
