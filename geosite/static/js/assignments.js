(function () {
  const search = document.getElementById("assignment-search");
  const engineers = Array.from(document.querySelectorAll("[data-engineer]"));
  const noMatches = document.getElementById("assignment-no-matches");
  search.addEventListener("input", () => {
    const term = search.value.trim().toLowerCase();
    for (const engineer of engineers) {
      engineer.hidden = !engineer.textContent.toLowerCase().includes(term);
      const radio = engineer.querySelector("input");
      radio.disabled = engineer.hidden;
      if (engineer.hidden) radio.checked = false;
    }
    noMatches.hidden = engineers.some(engineer => !engineer.hidden);
  });
})();
