(() => {
  const fileInput = document.getElementById("brand-logo");
  const cropPanel = document.getElementById("brand-logo-crop");
  const canvas = document.getElementById("brand-logo-crop-canvas");
  if (!fileInput || !cropPanel || !canvas) return;

  const context = canvas.getContext("2d");
  const cropStatus = document.getElementById("brand-logo-crop-status");
  const cropPreview = document.getElementById("brand-logo-crop-preview");
  const applyButton = document.getElementById("apply-logo-crop");
  const originalButton = document.getElementById("use-original-logo");
  const previewSize = 720;
  const maxPreviewHeight = 480;
  let sourceImage = null;
  let originalFile = null;
  let sourceUrl = null;
  let previewUrl = null;
  let selection = null;
  let dragStart = null;

  const setInputFile = (file) => {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    fileInput.files = transfer.files;
  };

  const draw = () => {
    if (!sourceImage || !context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(sourceImage, 0, 0, canvas.width, canvas.height);
    if (!selection) return;
    context.save();
    context.beginPath();
    context.rect(0, 0, canvas.width, canvas.height);
    context.rect(
      selection.x,
      selection.y,
      selection.size,
      selection.size,
    );
    context.fillStyle = "rgba(0, 0, 0, 0.48)";
    context.fill("evenodd");
    context.strokeStyle = "#fff";
    context.lineWidth = Math.max(2, canvas.width / 240);
    context.strokeRect(
      selection.x,
      selection.y,
      selection.size,
      selection.size,
    );
    context.restore();
  };

  const canvasPoint = (event) => {
    const bounds = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(canvas.width, (event.clientX - bounds.left) * canvas.width / bounds.width)),
      y: Math.max(0, Math.min(canvas.height, (event.clientY - bounds.top) * canvas.height / bounds.height)),
    };
  };

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    originalFile = file;
    sourceImage = null;
    selection = null;
    cropPreview.hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    originalButton.hidden = true;
    cropStatus.textContent = "Loading image…";
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      sourceImage = image;
      const scale = Math.min(
        previewSize / image.naturalWidth,
        maxPreviewHeight / image.naturalHeight,
        1,
      );
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      const size = Math.min(canvas.width, canvas.height);
      selection = {
        x: (canvas.width - size) / 2,
        y: (canvas.height - size) / 2,
        size,
      };
      cropPanel.hidden = false;
      cropStatus.textContent = "Drag to adjust the square crop, or upload the original image.";
      draw();
    };
    image.onerror = () => {
      sourceImage = null;
      cropPanel.hidden = false;
      cropStatus.textContent = "This image could not be previewed. You can still try uploading the original.";
    };
    image.src = sourceUrl;
  });

  canvas.addEventListener("pointerdown", (event) => {
    if (!sourceImage) return;
    dragStart = canvasPoint(event);
    canvas.setPointerCapture(event.pointerId);
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!dragStart) return;
    const point = canvasPoint(event);
    const deltaX = point.x - dragStart.x;
    const deltaY = point.y - dragStart.y;
    const size = Math.min(
      Math.abs(deltaX),
      Math.abs(deltaY),
      deltaX < 0 ? dragStart.x : canvas.width - dragStart.x,
      deltaY < 0 ? dragStart.y : canvas.height - dragStart.y,
    );
    selection = {
      x: deltaX < 0 ? dragStart.x - size : dragStart.x,
      y: deltaY < 0 ? dragStart.y - size : dragStart.y,
      size,
    };
    draw();
  });

  const finishSelection = () => {
    if (!dragStart) return;
    dragStart = null;
    if (selection.size < 16) {
      cropStatus.textContent = "Select a larger square area to crop.";
      return;
    }
    cropStatus.textContent = "Crop area selected.";
  };
  canvas.addEventListener("pointerup", finishSelection);
  canvas.addEventListener("pointercancel", finishSelection);

  canvas.addEventListener("keydown", (event) => {
    if (!sourceImage || !selection || !event.key.startsWith("Arrow")) return;
    event.preventDefault();
    const step = event.shiftKey ? 8 : 4;
    if (event.shiftKey) {
      const direction = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
      const size = Math.max(16, Math.min(
        Math.min(canvas.width, canvas.height),
        selection.size + direction * step,
      ));
      selection.size = size;
      selection.x = Math.min(selection.x, canvas.width - size);
      selection.y = Math.min(selection.y, canvas.height - size);
    } else {
      if (event.key === "ArrowLeft") selection.x = Math.max(0, selection.x - step);
      if (event.key === "ArrowRight") selection.x = Math.min(canvas.width - selection.size, selection.x + step);
      if (event.key === "ArrowUp") selection.y = Math.max(0, selection.y - step);
      if (event.key === "ArrowDown") selection.y = Math.min(canvas.height - selection.size, selection.y + step);
    }
    cropStatus.textContent = "Crop area adjusted.";
    draw();
  });

  applyButton.addEventListener("click", () => {
    if (!sourceImage || !selection || selection.size < 16) {
      cropStatus.textContent = "Select a square area to crop first.";
      return;
    }
    const output = document.createElement("canvas");
    output.width = 1024;
    output.height = 1024;
    const outputContext = output.getContext("2d");
    if (!outputContext) {
      cropStatus.textContent = "Cropping is unavailable in this browser.";
      return;
    }
    const scaleX = sourceImage.naturalWidth / canvas.width;
    const scaleY = sourceImage.naturalHeight / canvas.height;
    outputContext.drawImage(
      sourceImage,
      selection.x * scaleX,
      selection.y * scaleY,
      selection.size * scaleX,
      selection.size * scaleY,
      0,
      0,
      output.width,
      output.height,
    );
    applyButton.disabled = true;
    output.toBlob((blob) => {
      applyButton.disabled = false;
      if (!blob) {
        cropStatus.textContent = "The cropped image could not be created. Try another crop.";
        return;
      }
      try {
        const croppedFile = new File([blob], "company-logo.png", { type: "image/png" });
        setInputFile(croppedFile);
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(blob);
        cropPreview.src = previewUrl;
        cropPreview.hidden = false;
        originalButton.hidden = false;
        cropStatus.textContent = "Cropped logo ready to upload.";
      } catch {
        cropStatus.textContent = "This browser could not prepare the cropped image for upload.";
      }
    }, "image/png");
  });

  originalButton.addEventListener("click", () => {
    if (!originalFile) return;
    try {
      setInputFile(originalFile);
      cropPreview.hidden = true;
      originalButton.hidden = true;
      cropStatus.textContent = "Original image selected for upload.";
    } catch {
      cropStatus.textContent = "This browser could not restore the original image.";
    }
  });
})();
