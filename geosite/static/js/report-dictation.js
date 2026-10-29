(() => {
  const field = document.getElementById("activity-detail");
  const button = document.getElementById("report-dictate");
  const status = document.getElementById("dictation-status");
  const form = document.getElementById("site-report-form");
  if (!field || !button || !status || !form) return;

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let busy = false;
  let failed = false;
  let receivedText = false;
  let nativeRequest = false;
  let active = true;
  const setBusy = (value) => {
    busy = value;
    button.setAttribute("aria-pressed", String(value));
    button.textContent = value ? "\uD83C\uDFA4 Stop microphone" : "\uD83C\uDFA4 Start microphone";
    button.disabled = value && nativeRequest;
  };
  const append = (text) => {
    if (!active || typeof text !== "string" || !text.trim()) return;
    const separator = field.value && !/\s$/.test(field.value) ? "\n" : "";
    field.value += separator + text.trim();
    field.dispatchEvent(new Event("input", { bubbles: true }));
    receivedText = true;
    status.textContent = "Speech added. Review and edit the activity detail before submitting.";
  };
  const support = () => {
    if (busy) return;
    const supported = window.worktrackNativeSpeech === true || (window.isSecureContext && Recognition);
    button.disabled = !supported;
    status.textContent = supported
      ? "Ready for English voice typing."
      : "Voice typing is unavailable here. Use a supported HTTPS browser (such as Chrome), update the Android app, or type normally.";
  };
  window.addEventListener("worktrack-speech-ready", support);
  window.addEventListener("worktrack-speech-result", (event) => {
    if (!active || !busy || !nativeRequest) return;
    nativeRequest = false;
    setBusy(false);
    if (event.detail?.error) {
      status.textContent = event.detail.error;
    } else if (typeof event.detail?.text === "string" && event.detail.text.trim()) {
      append(event.detail.text);
    } else {
      status.textContent = "No speech received. Try again or type normally.";
    }
  });
  button.addEventListener("click", () => {
    if (busy) {
      recognition?.stop();
      status.textContent = "Finishing voice typing...";
      return;
    }
    failed = false;
    receivedText = false;
    if (window.worktrackNativeSpeech === true) {
      nativeRequest = true;
      setBusy(true);
      status.textContent = "Use the phone's English speech dialog. Cancel to keep typing normally.";
      window.location.href = "worktrack-voice://start";
      return;
    }
    if (!window.isSecureContext || !Recognition) {
      support();
      return;
    }
    recognition = new Recognition();
    recognition.lang = "en-IN";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        if (event.results[index].isFinal) append(event.results[index][0].transcript);
      }
    };
    recognition.onerror = (event) => {
      if (!active) return;
      failed = true;
      const messages = {
        "not-allowed": "Microphone permission was denied. Allow microphone access in browser settings or type normally.",
        "service-not-allowed": "Speech recognition is blocked by this browser. Use another supported browser or type normally.",
        "audio-capture": "No microphone is available. Connect or enable a microphone, or type normally.",
        "network": "Speech recognition could not connect. Check your internet connection or type normally.",
        "no-speech": "No speech was detected. Try again or type normally.",
        "aborted": "Voice typing cancelled. Your existing text is unchanged.",
        "language-not-supported": "English speech recognition is unavailable on this device. Type normally.",
      };
      status.textContent = messages[event.error] || "Voice typing failed. Try again or type normally.";
    };
    recognition.onend = () => {
      if (!active) return;
      setBusy(false);
      if (!failed && !receivedText) status.textContent = "No speech received. Try again or type normally.";
    };
    setBusy(true);
    status.textContent = "Listening in English... Speak, then pause or select Stop microphone.";
    try {
      recognition.start();
    } catch (error) {
      failed = true;
      setBusy(false);
      status.textContent = "The microphone could not start. Try again or type normally.";
      console.warn("Report voice typing could not start.", error.name);
    }
  });
  form.addEventListener("submit", (event) => {
    if (!busy) return;
    event.preventDefault();
    status.textContent = "Finish voice typing before submitting the report.";
  });
  window.addEventListener("pagehide", () => {
    active = false;
    recognition?.abort();
  });
  window.addEventListener("pageshow", () => {
    active = true;
    nativeRequest = false;
    setBusy(false);
    support();
  });
  support();
})();
