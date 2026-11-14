// Searchable country-code picker for the WhatsApp opt-in form. No dependencies.
// Markup: <div class="cc" data-picker><input type="hidden" name="cc"></div>
(() => {
  const C = `US United States 1|CA Canada 1|MX Mexico 52|BR Brazil 55|AR Argentina 54|CO Colombia 57|CL Chile 56|PE Peru 51|
EC Ecuador 593|VE Venezuela 58|BO Bolivia 591|PY Paraguay 595|UY Uruguay 598|GT Guatemala 502|HN Honduras 504|
SV El Salvador 503|NI Nicaragua 505|CR Costa Rica 506|PA Panama 507|CU Cuba 53|DO Dominican Republic 1|PR Puerto Rico 1|
JM Jamaica 1|HT Haiti 509|TT Trinidad and Tobago 1|BS Bahamas 1|BB Barbados 1|BZ Belize 501|GY Guyana 592|SR Suriname 597|
ES Spain 34|PT Portugal 351|FR France 33|DE Germany 49|IT Italy 39|GB United Kingdom 44|IE Ireland 353|NL Netherlands 31|
BE Belgium 32|LU Luxembourg 352|CH Switzerland 41|AT Austria 43|DK Denmark 45|SE Sweden 46|NO Norway 47|FI Finland 358|
IS Iceland 354|PL Poland 48|CZ Czechia 420|SK Slovakia 421|HU Hungary 36|RO Romania 40|BG Bulgaria 359|GR Greece 30|
HR Croatia 385|SI Slovenia 386|RS Serbia 381|BA Bosnia and Herzegovina 387|ME Montenegro 382|MK North Macedonia 389|
AL Albania 355|XK Kosovo 383|MD Moldova 373|UA Ukraine 380|BY Belarus 375|LT Lithuania 370|LV Latvia 371|EE Estonia 372|
RU Russia 7|TR Türkiye 90|CY Cyprus 357|MT Malta 356|AD Andorra 376|MC Monaco 377|SM San Marino 378|LI Liechtenstein 423|
GE Georgia 995|AM Armenia 374|AZ Azerbaijan 994|KZ Kazakhstan 7|UZ Uzbekistan 998|KG Kyrgyzstan 996|TJ Tajikistan 992|
TM Turkmenistan 993|MN Mongolia 976|CN China 86|HK Hong Kong 852|MO Macau 853|TW Taiwan 886|JP Japan 81|KR South Korea 82|
IN India 91|PK Pakistan 92|BD Bangladesh 880|LK Sri Lanka 94|NP Nepal 977|BT Bhutan 975|MV Maldives 960|AF Afghanistan 93|
IR Iran 98|IQ Iraq 964|SY Syria 963|LB Lebanon 961|JO Jordan 962|IL Israel 972|PS Palestine 970|SA Saudi Arabia 966|
AE United Arab Emirates 971|QA Qatar 974|BH Bahrain 973|KW Kuwait 965|OM Oman 968|YE Yemen 967|TH Thailand 66|VN Vietnam 84|
KH Cambodia 855|LA Laos 856|MM Myanmar 95|MY Malaysia 60|SG Singapore 65|ID Indonesia 62|PH Philippines 63|BN Brunei 673|
TL Timor-Leste 670|AU Australia 61|NZ New Zealand 64|PG Papua New Guinea 675|FJ Fiji 679|SB Solomon Islands 677|
VU Vanuatu 678|WS Samoa 685|TO Tonga 676|EG Egypt 20|LY Libya 218|TN Tunisia 216|DZ Algeria 213|MA Morocco 212|
SD Sudan 249|SS South Sudan 211|ET Ethiopia 251|ER Eritrea 291|DJ Djibouti 253|SO Somalia 252|KE Kenya 254|UG Uganda 256|
TZ Tanzania 255|RW Rwanda 250|BI Burundi 257|CD DR Congo 243|CG Congo 242|GA Gabon 241|CM Cameroon 237|CF Central African Republic 236|
TD Chad 235|NE Niger 227|NG Nigeria 234|BJ Benin 229|TG Togo 228|GH Ghana 233|CI Côte d’Ivoire 225|BF Burkina Faso 226|
ML Mali 223|SN Senegal 221|GM Gambia 220|GN Guinea 224|GW Guinea-Bissau 245|SL Sierra Leone 232|LR Liberia 231|
MR Mauritania 222|CV Cape Verde 238|GQ Equatorial Guinea 240|ST São Tomé and Príncipe 239|AO Angola 244|ZM Zambia 260|
ZW Zimbabwe 263|MW Malawi 265|MZ Mozambique 258|MG Madagascar 261|MU Mauritius 230|SC Seychelles 248|KM Comoros 269|
NA Namibia 264|BW Botswana 267|ZA South Africa 27|LS Lesotho 266|SZ Eswatini 268`;
  const COUNTRIES = C.split("|").map(s => s.trim()).filter(Boolean).map(s => {
    const m = s.match(/^(\w\w) (.+) (\d+)$/);
    return {iso: m[1], name: m[2], dial: m[3]};
  });
  const flag = iso => String.fromCodePoint(...[...iso].map(c => 127397 + c.charCodeAt(0)));
  const norm = s => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  function guessIso() {
    for (const l of navigator.languages || [navigator.language]) {
      const r = (l || "").split("-")[1];
      if (r && COUNTRIES.some(c => c.iso === r.toUpperCase())) return r.toUpperCase();
    }
    return "US";
  }

  document.querySelectorAll("[data-picker]").forEach(root => {
    const hidden = root.querySelector("input[type=hidden]");
    root.insertAdjacentHTML("beforeend", `
      <button type="button" class="cc-btn" aria-haspopup="listbox" aria-expanded="false" aria-label="Country code"></button>
      <div class="cc-pop" hidden>
        <input class="cc-search" type="text" placeholder="Search country or code" aria-label="Search country"
               autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="cc-list">
        <ul class="cc-list" id="cc-list" role="listbox"></ul>
      </div>`);
    const btn = root.querySelector(".cc-btn"), pop = root.querySelector(".cc-pop");
    const search = root.querySelector(".cc-search"), list = root.querySelector(".cc-list");
    let current = COUNTRIES.find(c => c.iso === guessIso()), shown = [], active = 0;

    const choose = c => {
      current = c; hidden.value = c.dial;
      btn.innerHTML = `<span class="f">${flag(c.iso)}</span>+${c.dial}<span class="car">▾</span>`;
      btn.title = c.name;
    };
    const render = () => {
      const q = norm(search.value.trim()).replace(/^\+/, "");
      shown = !q ? COUNTRIES : COUNTRIES.filter(c => norm(c.name).includes(q) || c.dial.startsWith(q) || c.iso.toLowerCase() === q)
        .sort((a, b) => (norm(b.name).startsWith(q) || b.dial === q) - (norm(a.name).startsWith(q) || a.dial === q));
      active = Math.max(0, shown.indexOf(current));
      if (q) active = 0;
      list.innerHTML = shown.length ? shown.map((c, i) =>
        `<li role="option" id="cc-${c.iso}" data-i="${i}" aria-selected="${c === current}" class="${i === active ? "on" : ""}">
           <span class="f">${flag(c.iso)}</span><span class="n">${c.name}</span><span class="d">+${c.dial}</span></li>`).join("")
        : `<li class="none">No match — type your number with + and its code</li>`;
      highlight();
    };
    const highlight = () => {
      list.querySelectorAll("li[data-i]").forEach(li => li.classList.toggle("on", +li.dataset.i === active));
      const el = list.querySelector(`li[data-i="${active}"]`);
      if (el) { el.scrollIntoView({block: "nearest"}); search.setAttribute("aria-activedescendant", el.id); }
    };
    const open = () => {
      pop.hidden = false; btn.setAttribute("aria-expanded", "true");
      search.value = ""; render(); search.focus();
    };
    const close = (refocus) => {
      pop.hidden = true; btn.setAttribute("aria-expanded", "false");
      if (refocus) btn.focus();
    };

    btn.addEventListener("click", () => pop.hidden ? open() : close(true));
    search.addEventListener("input", render);
    search.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); highlight(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); highlight(); }
      else if (e.key === "Enter") { e.preventDefault(); if (shown[active]) { choose(shown[active]); close(true); } }
      else if (e.key === "Escape") { e.preventDefault(); close(true); }
      else if (e.key === "Tab") close(false);
    });
    list.addEventListener("mousemove", e => {
      const li = e.target.closest("li[data-i]");
      if (li && +li.dataset.i !== active) { active = +li.dataset.i; highlight(); }
    });
    list.addEventListener("click", e => {
      const li = e.target.closest("li[data-i]");
      if (li) { choose(shown[+li.dataset.i]); close(true); root.closest("form")?.querySelector("input[type=tel]")?.focus(); }
    });
    document.addEventListener("pointerdown", e => { if (!pop.hidden && !root.contains(e.target)) close(false); });
    choose(current);
  });
})();
