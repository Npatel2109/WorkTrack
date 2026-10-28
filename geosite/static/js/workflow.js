(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const verifyButton = document.getElementById("verify-location");
  let nextUrl = null;

  verifyButton?.addEventListener("click", async () => {
    if (nextUrl) {
      window.location.assign(nextUrl);
      return;
    }
    const feedback = document.getElementById("gps-status");
    verifyButton.disabled = true;
    verifyButton.textContent = "Requesting GPS...";
    feedback.className = "gps-feedback mt-4";
    feedback.textContent = "Waiting for a precise location fix.";
    const showError = (message) => {
      feedback.className = "gps-feedback mt-4 error";
      feedback.textContent = message;
      verifyButton.disabled = false;
      verifyButton.textContent = "Try again";
    };
    const sendLocation = async (payload) => {
      try {
        const response = await fetch(verifyButton.dataset.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Location capture failed.");
        const outside = result.within_radius === false;
        feedback.className = outside ? "gps-feedback mt-4 error" : "gps-feedback mt-4 success";
        const distance = result.distance == null ? "" : ` Distance from assigned site: ${Number(result.distance).toFixed(1)} m.`;
        feedback.textContent = verifyButton.dataset.flow === "checkout"
          ? `Visit stopped. ${result.location_source === "last_known" ? "GPS unavailable; last captured location recorded." : result.location_source === "unavailable" ? "Location unavailable; this is recorded for the administrator." : outside ? "WARNING: You are outside the assigned site's reference radius." : "Stop location recorded."}${distance} Complete your report now or later.`
          : outside
            ? `WARNING: Outside the assigned site's reference radius.${distance} You can still continue.`
            : `Location captured.${distance}`;
        nextUrl = result.next;
        verifyButton.disabled = false;
        verifyButton.textContent = "Continue";
      } catch (error) {
        showError(error.message);
      }
    };
    const unavailable = async (reason, message) => {
      if (verifyButton.dataset.flow === "checkout") {
        feedback.textContent = `${message} Saving the last captured location.`;
        await sendLocation({ location_unavailable: true, reason });
      } else {
        showError(message);
      }
    };
    if (!window.isSecureContext) {
      await unavailable("insecure_context", "GPS requires HTTPS. Connect to your administrator's HTTPS server.");
      return;
    }
    if (!navigator.geolocation) {
      await unavailable("unsupported", "This browser does not support location services.");
      return;
    }
    navigator.geolocation.getCurrentPosition((position) => {
      sendLocation({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      });
    }, (error) => {
      unavailable(
        error.code === 1 ? "permission_denied" : error.code === 3 ? "timeout" : "position_unavailable",
        error.code === 1 ? "Location permission is off." : "Could not determine your current location."
      );
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
  });

  const video = document.getElementById("camera-video");
  if (video) {
    const start = document.getElementById("start-camera");
    const capture = document.getElementById("capture-photo");
    const retake = document.getElementById("retake-photo");
    const canvas = document.getElementById("camera-canvas");
    const preview = document.getElementById("captured-preview");
    const placeholder = document.getElementById("camera-placeholder");
    const errorBox = document.getElementById("camera-error");
    const submit = document.getElementById("submit-checkin");
    let stream;
    start.addEventListener("click", async () => {
      errorBox.classList.add("d-none");
      if (!window.isSecureContext) {
        errorBox.textContent = "The camera requires HTTPS. Connect to your administrator's HTTPS server to capture a live selfie.";
        errorBox.classList.remove("d-none");
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } },
        });
        video.srcObject = stream;
        await video.play();
        placeholder.classList.add("d-none");
        capture.disabled = false;
        start.classList.add("d-none");
      } catch (error) {
        errorBox.textContent = error.name === "NotAllowedError"
          ? "Allow camera access in your browser settings."
          : "Could not open the camera. Use a secure browser connection.";
        errorBox.classList.remove("d-none");
      }
    });
    capture.addEventListener("click", () => {
      if (!video.videoWidth) return;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      const data = canvas.toDataURL("image/jpeg", 0.88);
      document.getElementById("selfie-data").value = data;
      preview.src = data;
      preview.classList.remove("d-none");
      video.classList.add("d-none");
      capture.classList.add("d-none");
      retake.classList.remove("d-none");
      submit.disabled = false;
      stream?.getTracks().forEach((track) => track.stop());
    });
    retake.addEventListener("click", () => {
      preview.classList.add("d-none");
      video.classList.remove("d-none");
      document.getElementById("selfie-data").value = "";
      submit.disabled = true;
      capture.classList.remove("d-none");
      retake.classList.add("d-none");
      start.classList.remove("d-none");
    });
    window.addEventListener("pagehide", () => stream?.getTracks().forEach((track) => track.stop()));
    document.getElementById("checkin-form").addEventListener("submit", (event) => {
      if (!document.getElementById("selfie-data").value) {
        event.preventDefault();
        errorBox.textContent = "Capture a live selfie before checking in.";
        errorBox.classList.remove("d-none");
      }
    });
  }

})();
