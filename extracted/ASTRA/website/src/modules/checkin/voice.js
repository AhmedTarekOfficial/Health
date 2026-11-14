import { icon } from '../../shared/icons.js';
import { createSpeechSession } from './speech-session.js';

export function initVoice({ saveCheckin }) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const button = document.querySelector('#voice-toggle');
  const stage = document.querySelector('#voice-stage');
  const status = document.querySelector('#voice-status');
  const title = document.querySelector('#voice-title');
  const language = document.querySelector('#voice-language');
  const draft = document.querySelector('#voice-draft');
  const transcript = document.querySelector('#voice-transcript');
  const btnVoiceChat = document.querySelector('#btn-voice-chat');

  let writing = false;

  const session = createSpeechSession({
    Recognition,
    language: () => (language ? language.value : 'en-US'),
    postSpeechSilenceMs: 3500, // 3.5s of silence AFTER user speaks
    onState(state, message) {
      const listening = state === 'listening';
      const enabled = session.enabled;

      if (stage) {
        stage.classList.toggle('is-listening', listening);
        stage.classList.toggle('is-connecting', state === 'connecting');
      }

      if (button) {
        button.setAttribute('aria-pressed', String(enabled));
        button.setAttribute('aria-label', enabled ? 'Turn microphone off' : 'Enable microphone');
        button.innerHTML = `${icon('mic')}<span>${enabled ? 'Listening... Speak now' : 'Enable mic'}</span>`;
      }

      if (btnVoiceChat) {
        btnVoiceChat.classList.toggle('active', enabled);
        btnVoiceChat.setAttribute('aria-pressed', String(enabled));
        btnVoiceChat.setAttribute(
          'aria-label',
          enabled ? 'Stop listening and send transcript' : 'Toggle hands-free speech input'
        );
        btnVoiceChat.innerHTML = icon(enabled ? 'stop' : 'mic');
      }

      const chatBar = document.querySelector('.chat-input-bar');
      if (chatBar) chatBar.classList.toggle('is-voice-active', enabled);

      if (language) {
        language.disabled = enabled || !Recognition;
      }

      if (title) {
        title.textContent = listening
          ? 'Listening to your speech...'
          : state === 'connecting'
          ? 'Connecting to audio bus...'
          : 'Astronaut Voice Communication Bus';
      }

      if (status) {
        status.textContent = message;
      }
    },
    onWords(text) {
      // Barge-in: the astronaut's live voice stops any reply being read out.
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      if (transcript) transcript.textContent = text;
      if (draft) draft.hidden = !text;

      const chatInput = document.querySelector('#chat-input');
      if (chatInput) {
        chatInput.value = text;
        chatInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
    },
    onFinal(text) {
      const trimmed = (text || '').trim();
      if (!trimmed) return;

      saveCheckin(trimmed, null, { quiet: true });

      const chatInput = document.querySelector('#chat-input');
      if (chatInput) {
        chatInput.value = trimmed;
        chatInput.dispatchEvent(new Event('input', { bubbles: true }));
      }

      // Automatically submit recognized query to OrbitNexus AI after speaking!
      const chatForm = document.querySelector('#chat-form');
      if (chatForm && trimmed.length > 2) {
        chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
      }
    }
  });

  if (!Recognition) {
    if (button) {
      button.disabled = false;
      button.innerHTML = `${icon('mic')}<span>Simulate Voice</span>`;
    }
    if (status) {
      status.textContent = 'Web Speech API is unavailable in this browser. You can type or click the simulation button.';
    }
  }

  if (button) {
    button.addEventListener('click', () => {
      if (session.enabled) {
        session.disable();
      } else {
        if (window.speechSynthesis) window.speechSynthesis.cancel();
        session.enable();
      }
    });
  }

  return {
    session,
    pauseForWriting() {
      writing = true;
      session.disable();
    },
    resumeAfterWriting() {
      writing = false;
    }
  };
}
