import { addEntry } from '../mission/log.js';
import { initVoice } from './voice.js';
import { renderChatMessage } from './message-renderer.js';
import { renderReferenceSources } from './reference-sources.js';
import {
  isDisplayReady,
  isModeTransitionPending,
  resolveChatSnapshotRequest,
  waitForDisplayReady,
} from '../../shared/telemetry-context.js';

export function initCheckin(toast) {
  const form = document.querySelector('#feeling-form');
  const input = document.querySelector('#feeling-input');
  const dialog = document.querySelector('#writing-dialog');
  const opener = document.querySelector('#open-writing');
  let closeTimer;

  function saveCheckin(note, energy, { quiet = false } = {}) {
    addEntry(energy ? `Clinical Observation · ${energy} exertion` : 'Clinical Observation', note);
    const feedback = document.querySelector('#feeling-feedback');
    if (feedback) {
      feedback.hidden = false;
      feedback.textContent = 'Clinical observation archived in mission log.';
    }
    const routine = document.querySelector('[data-routine="3"]');
    if (routine) {
      routine.checked = true;
      routine.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (!quiet) toast('Clinical observation archived.');
  }

  function resizeInput() {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 300)}px`;
  }

  function openWriting(text) {
    voice.pauseForWriting();
    if (typeof text === 'string') {
      input.value = text;
      input.setCustomValidity('');
    }
    if (dialog.open) return;
    clearTimeout(closeTimer);
    dialog.classList.remove('closing');
    dialog.showModal();
    resizeInput();
    input.focus();
  }

  function closeWriting() {
    if (!dialog.open || dialog.classList.contains('closing')) return;
    dialog.classList.add('closing');
    const duration = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 160;
    closeTimer = setTimeout(() => {
      dialog.close();
      voice.resumeAfterWriting();
      dialog.classList.remove('closing');
      if (opener) opener.focus({ preventScroll: true });
    }, duration);
  }

  const voice = initVoice({ saveCheckin, openWriting });

  if (opener) opener.addEventListener('click', () => openWriting());
  const closeBtn = document.querySelector('#close-writing');
  if (closeBtn) closeBtn.addEventListener('click', closeWriting);

  if (dialog) {
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      closeWriting();
    });
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        closeWriting();
    });
  }

  if (input) {
    input.addEventListener('input', () => {
      input.setCustomValidity('');
      resizeInput();
    });
  }

  if (form) {
    form.addEventListener('submit', event => {
      event.preventDefault();
      const note = input.value.trim();
      if (!note) {
        input.setCustomValidity('Add clinical observations.');
        input.reportValidity();
        return;
      }
      saveCheckin(note, new FormData(form).get('energy'));
      form.reset();
      input.style.height = '';
      closeWriting();
    });
  }

  // Initialize the OrbitNexus AI Chatbot & Voice Bridge
  initAIChatbot(voice, toast);
}

function initAIChatbot(voice, toast) {
  const chatForm = document.querySelector('#chat-form');
  const chatInput = document.querySelector('#chat-input');
  const chatHistory = document.querySelector('#chat-history');
  const btnVoiceChat = document.querySelector('#btn-voice-chat');
  const btnSimulateVoice = document.querySelector('#btn-simulate-voice');
  if (chatInput) chatInput.dir = 'auto';
  chatHistory?.querySelectorAll('.bubble-text').forEach(node => {
    renderChatMessage(node, node.textContent.trim());
  });

  // Handle Voice Toggle inside Chat
  if (btnVoiceChat && voice && voice.session) {
    btnVoiceChat.addEventListener('click', () => {
      if (voice.session.enabled) {
        voice.session.disable();
        const text = (chatInput ? chatInput.value : '').trim();
        if (text) {
          submitChatMessage(text);
        } else {
          toast('Microphone paused.');
        }
      } else {
        // Speaking takes priority over the assistant's voice (barge-in).
        stopSpeaking();
        voice.session.enable();
        toast('Microphone active. Speak now.');
      }
    });
  }

  // Simulated Voice Input for fallback & demonstration
  if (btnSimulateVoice) {
    btnSimulateVoice.addEventListener('click', () => {
      const sampleText = "Commander Vance requesting acute cephalad fluid shift and SANS risk assessment";
      simulateVoiceTyping(sampleText);
    });
  }

  function simulateVoiceTyping(fullText) {
    if (!chatInput) return;
    chatInput.value = '';
    chatForm?.classList.add('is-voice-active');
    toast('Transcribing simulated astronaut voice input...');

    let i = 0;
    const interval = setInterval(() => {
      if (i < fullText.length) {
        chatInput.value += fullText.charAt(i);
        chatInput.dispatchEvent(new Event('input', { bubbles: true }));
        i++;
      } else {
        clearInterval(interval);
        chatForm?.classList.remove('is-voice-active');
        setTimeout(() => {
          submitChatMessage(fullText);
        }, 400);
      }
    }, 25);
  }

  // Handle Chat Submission
  if (chatForm) {
    chatForm.addEventListener('submit', event => {
      event.preventDefault();
      const text = (chatInput ? chatInput.value : '').trim();
      if (!text) return;
      submitChatMessage(text);
    });
  }

  let chatSubmitting = false;

  async function submitChatMessage(message) {
    if (!message || chatSubmitting) return;
    stopSpeaking();
    chatSubmitting = true;

    try {
      // Do not append bubbles or clear the input until we have an explicit snapshot
      // contract — never send snapshot_id=null for the server to substitute blindly.
      let waited = false;
      let ready = isDisplayReady();
      if (isModeTransitionPending() || !ready) {
        ready = await waitForDisplayReady(2500);
        waited = true;
      }

      const resolution = resolveChatSnapshotRequest({ waited, ready });
      if (!resolution.ok) {
        if (chatInput) chatInput.value = message;
        toast('Telemetry display not ready. Message restored — retry when readings are bound.');
        return;
      }

      const snapshotId = resolution.snapshot_id;
      if (chatInput) chatInput.value = '';
      appendChatBubble('user', 'Astronaut Alex', message);
      const typingId = appendChatBubble(
        'ai',
        'OrbitNexus AI',
        'Checking your question and available references...'
      );

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message,
            snapshot_id: snapshotId,
          }),
        });
        const data = await res.json();
        const reply = data.reply || data.error || 'Medical intelligence offline.';

        const typingEl = document.querySelector(`#${typingId}`);
        if (typingEl) {
          renderChatMessage(typingEl.querySelector('.bubble-text'), reply);
          renderReferenceSources(typingEl, data.knowledge_sources, data.knowledge_status, reply);
        }

        // Read the actual assistant answer, not just the chat bubble.
        speakReply((data.voice && data.voice.text) || reply);

        addEntry(
          'OrbitNexus AI Clinical Analysis',
          `Astronaut: ${message}\n\nOrbitNexus: ${reply}`
        );
      } catch (err) {
        const typingEl = document.querySelector(`#${typingId}`);
        if (typingEl) {
          renderChatMessage(
            typingEl.querySelector('.bubble-text'),
            'Network error communicating with OrbitNexus AI.'
          );
        }
      }
    } finally {
      chatSubmitting = false;
    }
  }

  let speechToken = 0;

  // Silences the assistant immediately so a new question or the astronaut's
  // own voice always takes priority over a reply that is still being read.
  function stopSpeaking() {
    const synth = window.speechSynthesis;
    speechToken++; // invalidate any utterance still queued to start
    if (synth && (synth.speaking || synth.pending)) synth.cancel();
  }

  function spokenText(raw) {
    return String(raw || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`([^`]*)`/g, '$1')
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/https?:\/\/\S+/g, ' ')
      .replace(/[*_~>#|]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  // The reply may arrive in Arabic while the browser default voice is English;
  // speaking Arabic text with an English voice is silent or unintelligible.
  function speechLanguage(text) {
    const picker = document.querySelector('#voice-language');
    const preferred = (picker && picker.value) || 'en-US';
    if (/[\u0600-\u06FF]/.test(text)) {
      return preferred.toLowerCase().startsWith('ar') ? preferred : 'ar-EG';
    }
    return preferred.toLowerCase().startsWith('en') ? preferred : 'en-US';
  }

  function matchingVoice(synth, lang) {
    const voices = synth.getVoices() || [];
    const exact = voices.find(v => v.lang && v.lang.toLowerCase() === lang.toLowerCase());
    if (exact) return exact;
    const base = lang.split('-')[0].toLowerCase();
    return voices.find(v => v.lang && v.lang.toLowerCase().startsWith(base)) || null;
  }

  // Some browsers expose voices asynchronously; give them a moment before speaking.
  function whenVoicesReady(synth, callback) {
    if ((synth.getVoices() || []).length) {
      callback();
      return;
    }
    let fired = false;
    const run = () => {
      if (fired) return;
      fired = true;
      if (synth.removeEventListener) synth.removeEventListener('voiceschanged', run);
      callback();
    };
    if (synth.addEventListener) synth.addEventListener('voiceschanged', run);
    setTimeout(run, 800);
  }

  // Chrome drops an utterance queued in the same task as cancel() and can leave
  // the engine paused, so resume it and start speech on the next tick.
  function speakReply(raw) {
    const synth = window.speechSynthesis;
    if (!synth || typeof SpeechSynthesisUtterance === 'undefined') return;
    const text = spokenText(raw);
    if (!text) return;

    const token = ++speechToken;
    const lang = speechLanguage(text);
    synth.cancel();
    if (synth.paused) synth.resume();

    setTimeout(() => {
      if (token !== speechToken) return;
      whenVoicesReady(synth, () => {
        if (token !== speechToken) return;
        const voice = matchingVoice(synth, lang);
        if (!voice && !lang.toLowerCase().startsWith('en')) {
          toast('No voice installed for this language. Add one in your browser/OS speech settings.');
        }
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = lang;
        if (voice) utterance.voice = voice;
        utterance.rate = 1.05;
        synth.speak(utterance);
      });
    }, 80);
  }

  function appendChatBubble(role, sender, text) {
    if (!chatHistory) return '';
    const id = `bubble-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`;
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;
    bubble.id = id;
    const author = document.createElement('div');
    author.className = 'sender';
    author.dir = 'auto';
    author.textContent = sender;
    const content = document.createElement('div');
    content.className = 'bubble-text';
    renderChatMessage(content, text);
    bubble.append(author, content);
    chatHistory.appendChild(bubble);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    return id;
  }

}
