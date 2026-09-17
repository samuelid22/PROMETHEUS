const DEFAULT_WORDMARK = "Prometheus";

/**
 * Presentation-only logo primitive. Set `asset` to use a supplied final logo
 * image; omit it to use the current triangle-and-dot placeholder mark.
 * `variant="compact"` renders the app/icon form without the wordmark.
 */
class PrometheusLogo extends HTMLElement {
  connectedCallback() {
    if (this.dataset.rendered === "true") return;
    this.dataset.rendered = "true";

    const compact = this.getAttribute("variant") === "compact";
    const asset = this.getAttribute("asset");
    const wordmark = this.getAttribute("wordmark") || DEFAULT_WORDMARK;
    this.classList.add("prometheus-logo", compact ? "prometheus-logo--compact" : "prometheus-logo--primary");

    if (asset) {
      const image = document.createElement("img");
      image.className = "brand-asset";
      image.src = asset;
      image.alt = compact ? "" : wordmark;
      if (compact) image.setAttribute("aria-hidden", "true");
      this.appendChild(image);
      return;
    }

    const mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    mark.setAttribute("class", "brand-mark");
    mark.setAttribute("viewBox", "0 0 40 40");
    mark.setAttribute("aria-hidden", "true");
    mark.innerHTML = '<path d="M20 4 36 33H4L20 4Z"/><circle cx="20" cy="24" r="4.5"/>';
    this.appendChild(mark);

    if (!compact) {
      const name = document.createElement("span");
      name.className = "brand-wordmark";
      name.textContent = wordmark;
      this.appendChild(name);
    }
  }
}

customElements.define("prometheus-logo", PrometheusLogo);
