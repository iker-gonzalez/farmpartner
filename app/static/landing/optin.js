// WhatsApp opt-in, shared by the landing hero form and the header form (any form[data-optin]).
// FarmPartner sends the first WhatsApp message (approved template). If it can't yet,
// it opens WhatsApp with "hi" prefilled so the farmer starts the chat instead.
(() => {
  document.querySelectorAll("form[data-optin]").forEach(form => {
    const status = form.querySelector(".status");
    const submit = form.querySelector("button[type=submit]");
    const say = (html, err) => {
      status.innerHTML = html;
      status.classList.toggle("err", !!err);
      form.classList.toggle("has-status", !!html);
    };
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const raw = form.phone.value.trim();
      const digits = raw.replace(/\D/g, "");
      if (digits.length < 6) return say("Enter your WhatsApp number.", true);
      if (!form.consent.checked) return say("Tick the box so we can message you on WhatsApp.", true);
      const phone = raw.startsWith("+") || !form.cc.value ? "+" + digits : "+" + form.cc.value + digits.replace(/^0+/, "");
      submit.disabled = true; say("Sending…");
      try {
        const r = await fetch("/api/optin", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({phone, consent: true})});
        const d = await r.json().catch(() => ({}));
        if (!r.ok) say(d.detail || "Something went wrong — try again.", true);
        else if (d.sent) say(d.again ? "Already sent — check WhatsApp for FarmPartner’s message. 📲"
                                     : "✓ Check WhatsApp — FarmPartner just messaged you. Tap <b>Get started</b>. 📲");
        else { say(`Opening WhatsApp — just send the “hi” message. <a href="${d.wa_url}" target="_blank" rel="noopener">Didn’t open?</a>`);
               setTimeout(() => { location.href = d.wa_url; }, 600); }
      } catch { say("Network error — try again.", true); }
      submit.disabled = false;
    });
    // Hide a stale message once the visitor clicks elsewhere.
    document.addEventListener("pointerdown", e => {
      if (!form.contains(e.target) && form.classList.contains("has-status") && !submit.disabled) say("");
    });
  });

  // Header on mobile: the compact WhatsApp button opens / closes the form panel.
  document.querySelectorAll(".nav-toggle").forEach(btn => {
    const panel = document.getElementById(btn.getAttribute("aria-controls"));
    if (!panel) return;
    const set = open => {
      panel.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", String(open));
      if (open) panel.querySelector("input[type=tel]")?.focus();
    };
    btn.addEventListener("click", () => set(!panel.classList.contains("open")));
    document.addEventListener("keydown", e => { if (e.key === "Escape" && panel.classList.contains("open")) { set(false); btn.focus(); } });
    document.addEventListener("pointerdown", e => {
      if (panel.classList.contains("open") && !panel.contains(e.target) && !btn.contains(e.target)) set(false);
    });
  });
})();
