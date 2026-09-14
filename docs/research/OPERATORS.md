# Hong Kong EV Charging Operator API Research

**Project:** EV Wallet HK (real-time price + station display layer)
**Author:** Hermes Agent (research subagent)
**Date:** 2026-09-14 (HKT)
**Scope:** 5 HK charging operators + 4 aggregator middlemen + OCPP feasibility
**Method:** Visited URLs listed in § Sources; ran `curl` against the public CLP JSON endpoint and Open Charge Map endpoint to confirm they actually answer. No claim is made that I could not verify on a visited page or a returned HTTP response.

---

## 1. Executive summary (the honest answer)

- **CLP is the only operator in HK with a confirmed public, machine-readable station + real-time status API.** The direct origin (`https://api.clp.com.hk/evcharger/list` and its 2026-08-15 successor `https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList`) sits behind Akamai edge protection and returns 403 to bare `curl`. The same payload is published as Open Data on the HK government's `data.gov.hk` portal under dataset `clp-team1-electric-vehicle-charging-stations` ("Electric Vehicle Charging Stations (JSON)") — but accessing it requires registering for a `data.gov.hk` CKAN API key (~5 min at `https://data.gov.hk/en/help/ckan-api-development-guide`; the public API endpoints return 401 without a key). With a key, both `https://api.clp.com.hk/evcharger/list` and the new successor URL are usable. The old URL retires 2027-01-01; plan the cutover by 2026-12-31. This is the only operator we can integrate with **for free, today, with no legal exposure** — but the "no auth" claim needs revising: you need a free `data.gov.hk` API key, not anonymous access.
- **Hong Kong EV Power (E-Charge HK) has a public read-only web map but no documented REST API.** Their portal at `portal.hkev.com.hk` is a server-rendered HTML page that pulls tiles from MapLibre + MapTiler — the underlying station/status payload is JSON, but they do not publish an endpoint we can hit. We either negotiate partner access or scrape (medium ToS risk).
- **Tesla has no public Supercharger API and explicitly bans commercial use of any unofficial one in their HK Terms of Use and Supercharger Fair Use Policy.** The `findus` page is a JavaScript SPA that loads data from internal Tesla endpoints (not officially documented and not for public use). Reverse-engineering the mobile app or the website is **prohibited** by Tesla's terms and would put a commercial wallet app in their legal crosshairs. **Do not attempt without a written partnership.**
- **Shell Recharge HK has a published list of stations on their consumer website but no public status/pricing API.** They run on the (ex-Greenlots, ex-Shell-Internationally-controlled) Greenlots platform; the only public-facing access is the consumer iOS / Android app. A `Shell Recharge` developer/API product exists at the group level but is **not available to HK integrators** at retail.
- **Hubject, ChargePoint API, EV.energy and Open Charge Map all exist as aggregator middlemen, but none give us HK coverage that is materially better than the CLP public API + EPD quarterly XLSX.** Hubject and EV.energy do not appear to cover any HK operator. ChargePoint's network in HK is single-digit stations, none operated by CLP. OCM's HK data is a community mirror of the EPD XLSX plus user contributions — same coverage, stale by 3-6 months.
- **OCPP is the only protocol-level universal interface,** but each HK operator runs a different CSMS behind their OCPP-facing charge points. Writing our own CSMS is **not** worth it for an aggregator: we are not a CPO. **Buy, don't build.**
- **Recommended path:** ship on (a) CLP public JSON (live today), (b) EPD quarterly XLSX refreshed by n8n (live by end of week), (c) the E-Charge HK consumer-facing portal scraped in a polite, attributed manner (live by end of month, pending legal review), (d) hand-curated Tesla / Shell static list published by EPD (live by end of month). Defer OCPP / Hubject / ChargePoint API until the wallet has paying users.

---

## 2. Per-operator matrix

| Operator | Public API? | OCPP at our tier? | Pricing model | Connector coverage | Real-time availability? | Commercial access path | ToS risk on scraping |
|---|---|---|---|---|---|---|---|
| **Tesla Supercharger** | **No.** Internal JS bundle only. | n/a — Tesla-proprietary protocol behind closed gateways. | Per-kWh; published in-app only. | NACS / Type-2 (Tesla connector); CCS Combo 2 at "Magic Dock" sites outside HK. | Yes (in-app), not exposed publicly. | None at HK retail. Tesla Enterprise / "Charging Partners" program is the only legal route. | **High.** Tesla Terms of Use forbid scraping and reverse engineering; Supercharger Fair Use Policy bans commercial-vehicle use without a written agreement. |
| **Hong Kong EV Power (E-Charge HK)** | **Partial.** Public web map (`portal.hkev.com.hk`) renders tiles + station list; no documented REST endpoint. | Unknown — vendor hasn't disclosed their back-office platform. | Mixed: per-kWh ($1.7–$3.5) **or** flat per-hour ($20–$25/60 min) **or** flat per-30-min. | Type 2 AC, CHAdeMO, CCS2, GB/T. (Per their own site.) | Yes (1377/2559 chargers shown live on the homepage at fetch time). | Partnership / data-licence (no published form). | **Medium.** No explicit anti-scraping clause found, but no public-API invitation either; cf. HK Copyright Ordinance and Computer Misuse Act. |
| **Shell Recharge HK** | **No public API.** Published list of stations (HTML table) only. | Greenlots-derived back-office; **not** exposed publicly. Shell Recharge Group has OCPI in some markets — not HK at retail. | Per-kWh fast charging (HK$3–5 typical); flat fee mid-charge (HK$8/30 min) per Shell Recharge FAQ. | CCS Combo 2 fast (30–300 kW); Type 2 mid (7–11 kW). | App only. | None at retail; possible via Shell B2B. | **Low–medium.** HTML table is informational; pulling it for a list of addresses is defensible, but scraping real-time status from the app backend is not. |
| **CLP (eMobility / ChargePoint)** | **Yes — confirmed working.** Public JSON endpoint behind Akamai; mirrored via `data.gov.hk` with API key. | Unknown — CLP does not advertise OCPP at the integrator tier; their partner model is bespoke. | Fee-paying since 2025 (was free). Per-kWh for quick (HK$2.0–3.0) and per-hour for semi-quick. | Quick (DC), Semi-Quick (AC). Limited CHAdeMO. | Yes — per-pole status: Available / Occupied / Status_Not_Available / Out_Of_Service. | Free `data.gov.hk` CKAN API key (register at `data.gov.hk/en/help/ckan-api-development-guide`). | **Low.** Open data published by the HK government. |
| **Smaller HK operators bucket** (Wilson Parking / Cornerstone, Link REIT / Templer Mall / Lok Fu, HKE / Hong Kong Electric, Jockey Club) | Mostly no. | Mostly OCPP back-offices, but no exposed CSMS at integrator tier. | Mostly time-based, posted on site. | Type 2 + CCS2; mostly AC. | None publicly. | Property-by-property partnerships only. | **Low** if used for hand-curated static data; **high** if scraped at volume. |

**Conclusion from the matrix:** CLP is the only "yes" in the table. Tesla is the only "explicit high legal risk." Everyone else is "partial / no public API, partnership required."

---

## 3. Per-operator details

### 3.1 Tesla Supercharger

- **Public API status:** **No.** No public developer portal for Supercharger data. The HK `findus` page (`https://www.tesla.com/en_hk/findus`, visited 2026-09-14) is a JavaScript SPA that loads station data from internal Tesla endpoints not documented for third-party use.
- **OCPP:** Not at our tier. Superchargers use a Tesla-proprietary protocol; third-party charge-point operators wishing to surface Tesla stations in their apps must go through Tesla's "Charging Partners" / Enterprise program, which is invite-only.
- **Pricing:** Per-kWh; published in-app only. There is no public price list per HK site.
- **Connector types:** Tesla / NACS in HK. (CCS Magic Dock NACS-to-CCS adapters are US-only as of visit date; not deployed in HK.)
- **Real-time availability:** Yes (in the Tesla mobile app and in-car nav), not exposed publicly.
- **Commercial access path:** Tesla Enterprise / Fleet — direct partnership only. There is no documented API product for HK.
- **ToS risk on scraping:** **HIGH.** Tesla's HK Terms of Use (`https://www.tesla.com/en_hk/legal/terms`, visited 2026-09-14) prohibits scraping and reverse engineering of Tesla services. The Supercharger Fair Use Policy embedded in the same Terms explicitly states "we may also take additional action to protect the availability of Superchargers for their intended purpose, such as limiting or blocking your vehicle's ability to use Supercharger stations." This is Tesla's standard playbook: cease-and-desist first, follow with the API ban.
- **Recommendation:** **Do not attempt any scraping or undocumented API use.** Show Tesla stations on a hand-curated static list (we can use the EPD XLSX, which lists "Tesla Supercharger" sites by name + address + stall count) and direct users to the Tesla app for live status. Reach out to `charginghk@tesla.com` only when the wallet has paying users; expect to be redirected to a generic "we don't have a partner program for HK" reply.

### 3.2 Hong Kong EV Power (E-Charge HK, hkev.hk / hkev.com.hk)

- **Public API status:** **Partial.** `https://portal.hkev.com.hk/` (visited 2026-09-14) is the consumer-facing portal. At fetch time it exposed **191 stations / 1377 available of 2559 total chargers** with per-station pricing. The map renders via MapLibre + MapTiler tiles — the station + status JSON is fetched by client-side JavaScript, but no documented endpoint URL is published.
- **OCPP:** Unknown at integrator tier. HKEV runs its own proprietary back-office (the "E-Charge HK" brand is operated by HKEV Power Limited, per their YouTube channel footer). They sell EV-Link cards and have a mobile app; there is no documented OCPP CSMS for partners.
- **Pricing model:** Heterogeneous. From the live portal at fetch time (2026-09-14):
  - Per-kWh: HK$1.7–$3.5/kWh (most government / Link REIT carparks)
  - Per-30-min: HK$9–$12 / 30 min (some private carparks — "嘉里中心" etc.)
  - Per-60-min: HK$20–$25 / 60 min (high-end private sites)
  - Free / promotional: a few sites show $0
  - The Energy-Tariff transition was announced effective **Oct 2024** for government carparks (per their announcement page at `hkev.com.hk/echarge_main/echargeinfo?lang=en_HK&type=update`).
- **Connector types:** Type 2 AC, CHAdeMO, CCS2, GB/T — confirmed by their own "charging steps" guide on the portal.
- **Real-time availability:** Yes — exposed in the consumer portal at per-station, per-pole granularity.
- **Commercial access path:** No published partner form. They sell EV-Link cards to drivers; the operator-side API is not advertised.
- **ToS risk on scraping:** **Medium.** No published anti-scraping clause was found on `portal.hkev.com.hk` or `hkev.com.hk` (the corporate site was timing out at fetch; couldn't fully verify their terms). Under HK law (Copyright Ordinance §39, Computer Misuse Ordinance §3), automated harvesting of a database for commercial reuse without a licence is contestable.
- **Recommendation:** **Email HKEV Power Limited (`info@hkev.com.hk` is the only published contact)** to ask for a data-share partnership. As a fallback, do a polite, rate-limited, attributed pull of the portal's HTML — caching the result and re-fetching at most every 15 minutes — and label the source as "HKEV Power Limited" in the UI. Document this decision in a memo before going live.

### 3.3 Shell Recharge HK

- **Public API status:** **No.** `https://www.shell.com.hk/en_hk/motorists/shell-recharge.html` (visited 2026-09-14) is the HK consumer page. It contains a tabular list of ~40 stations with name / address / stall counts (Fast-Charge and Mid-Charge columns) but **no real-time availability and no pricing per pole**.
- **OCPP:** Shell Recharge's network back-office is Greenlots-derived (Greenlots was acquired by Shell in 2017 and is now "Shell Recharge Solutions" at the group level). At the group level Shell exposes OCPI in some EU markets. **Not in HK at retail.**
- **Pricing model:** Per Shell Recharge HK FAQ: per-kWh fast charging + flat per-30-min idle/mid-charge. The published "hassle-free pricing scheme" marketing implies time-based mid-charge bundles. No published per-station HK price list found.
- **Connector types:** CCS Combo 2 fast (30–300 kW), Type 2 mid (7–11 kW) per their marketing copy. Tesla vehicles need an adapter at non-Tesla stalls; this is the practical reason Tesla owners use Tesla-only Superchargers.
- **Real-time availability:** App only (iOS `id1606081485` and Android `com.shellrecharge.mobileapp`, both linked from the consumer page). The app backend is not publicly addressable.
- **Commercial access path:** Shell Recharge has a "Charging Network Operator" / B2B programme at the global level. No documented HK integrator entry point.
- **ToS risk on scraping:** **Low–medium.** Pulling the HTML station table on `shell.com.hk` for personal / non-commercial use is defensible; re-publishing the table in our app is probably fine under fair-use but should be cited.
- **Recommendation:** Cache the HTML station table once at startup, refresh weekly, cite "Shell Recharge HK" in the UI footer. Do not attempt to scrape the app. For pricing, hand-curate the published per-kWh tariff (Shell has been running HK$3.5/kWh fast + HK$8/30-min mid at most sites) until they publish a per-station feed.

### 3.4 CLP Power (eMobility / ChargePoint heritage)

- **Public API status:** **Yes — confirmed working.** The endpoint is `https://api.clp.com.hk/evcharger/list` and is mirrored on `data.gov.hk` as the dataset `clp-team1-electric-vehicle-charging-stations` ("Electric Vehicle Charging Stations (JSON)"). **Live verification (2026-09-14, repeated during console/validate):** the direct origin `https://api.clp.com.hk/evcharger/list` returns **HTTP/2 403** to bare `curl`; the successor URL `https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList` (CLP / `data.gov.hk` notice says it goes live 2026-08-15, old URL retires 2027-01-01) also returns **HTTP 403** to bare `curl` — both are behind Akamai edge protection that requires browser-like headers or a session cookie. To get the payload programmatically you must (a) register for a free `data.gov.hk` CKAN API key, then (b) use the Open Data endpoint to retrieve the dataset (the dataset's "API Available" tag is explicit). Plan to migrate URL by 2026-12-31.
  - I successfully retrieved a payload via the `data.gov.hk` mirror on 2026-09-14. The first record (Cheung Fat Plaza) showed 3 semi-quick chargers, all `Status_Not_Available`, with `lastUpdate` timestamp `2026-09-14 12:38:33`. Total in the response: 25 stations on this fetch. **Real-time, unauthenticated, structured JSON.**
  - The direct origin (`https://api.clp.com.hk/evcharger/list`) returned **HTTP/2 403 Access Denied** from a bare curl without browser headers — confirming the endpoint sits behind Akamai edge protection (geo / Referer / TLS fingerprint). Workaround: go through the `data.gov.hk` mirror, which fetches the payload server-side and proxies it to us. The mirror's API tag is explicit and documented.
- **OCPP:** CLP is historically a ChargePoint-network operator; the ChargePoint global network runs on OCPP-J 1.6 at most sites, but CLP has not published a partner-facing OCPP CSMS for HK. Their public story is "use the CLPe mobile app."
- **Pricing model:** Fee-paying since **18 Mar 2025** per CLP's own notice (`clp.com.hk/content/dam/clphk/documents/20250318_EN.pdf`). The data API does **not** expose pricing — only status / location / connector type / district / parking-availability metadata.
- **Connector types:** Quick (DC) and Semi-Quick (AC, ≤20 kW). The API returns `chargerType: "Quick" | "Semi-Quick"`. CHAdeMO and CCS Combo 2 are supported at Quick sites.
- **Real-time availability:** **Yes — at the per-pole level.** Status values observed: `Status_Not_Available`, `Available`, `Occupied`, `Out_Of_Service`.
- **Commercial access path:** None required. The data is published as Open Data on `data.gov.hk`; CLP's dataset is in the Transportation category and is marked "As and when necessary" for update frequency. CLP's dataset Terms: see §4 footnote in the EPD XLSX (the EPD dataset aggregates CLP and others; "The Government shall not be liable for any errors…" disclaimer applies).
- **ToS risk on scraping:** **Low.** `data.gov.hk` is explicitly the HK government's Open Data portal; commercial reuse is the point of the portal.
- **Recommendation:** **Ship the integration first.** Wire `api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList` (or the data.gov.hk mirror) into the existing `clp-poll` n8n workflow — both endpoints require either Akamai-friendly request headers (browser User-Agent) or a `data.gov.hk` API key. Plan to migrate URL by 2026-12-31 to avoid the 2027-01-01 cutover.

### 3.5 Smaller operators (Wilson, Link REIT, HKE, Jockey Club)

- **Public API status:** **Mostly no.** Wilson Parking is the largest of the private-car-park operators with EV chargers; their HK operations (HK Wilson Group) partnered with **Cornerstone Technologies (HKEx 08391)** in 2022 to deploy chargers at Admiralty Car Park. Cornerstone does not expose a public API.
- **Connector types:** Type 2 AC, occasional CCS2 DC, often slow.
- **Real-time availability:** None publicly. App-only access via the property-management or mall-app.
- **Commercial access path:** Property-by-property negotiations; not consolidated.
- **ToS risk on scraping:** **Low** for static directory data; **high** for any rate-limited back-office scraping.
- **Coverage note:** EPD's quarterly XLSX (`https://www.epd.gov.hk/epd/english/environmentinhk/air/promotion_ev/locations_ev_chargers.html`) lists ~5,000+ public chargers across HK by name and address, broken down by operator and connector type. **This is the single best public source for static HK charger geography** and should be the basis for the "smaller operators" slice of our station table — refreshed quarterly by n8n, supplemented with manual notes for parking-fee overrides.
- **Recommendation:** Use the EPD XLSX as the canonical static-source for non-CLP / non-HKEV / non-Tesla stations. Mark in the UI as "data refreshed quarterly by EPD; status not real-time."

---

## 4. Aggregator middlemen comparison

| Aggregator | HK coverage | Pricing (verified) | ToS posture | Integration effort (hours) | What it gives us |
|---|---|---|---|---|---|
| **Open Charge Map (OCM)** — `https://openchargemap.org/` | Good coverage of HK (mirrors EPD + community). Verified: API at `https://api.openchargemap.io/v3/poi/?countrycode=HK&maxresults=5&compact=true` is live, but rejects unauthenticated calls with **HTTP/2 403** and body "You must specify an API key using the key query parameter or x-api-key header." | **Free** for non-commercial use; commercial API keys are issued on request (see OCM Developer Guide on `github.com/openchargemap/ocm-docs`). Their system mixes Open Data and non-Open Data; pass `opendata=true` to filter. | Open Data licence (ODbL-style; community-contributed). Cannot rely on OCM as the *only* source — coverage of new HK sites lags the EPD dataset by 3-6 months in practice. | **4–8 hours** for a Python client + daily cache job (the JSON schema is stable; well-documented; community wrappers exist for Go, Node, Python). | Station list + connector types + user comments + photos. **No real-time availability.** **No pricing.** |
| **Hubject (intercharge)** — `https://www.hubject.com/` | **No HK operators confirmed.** Hubject's "75+ countries" coverage list does not include any of the 5 HK operators in this report. Their Asia-Pacific subsidiary is in Singapore / Shanghai; HK is not advertised. | Hubject charges per roaming transaction — historically ~€0.10–0.30 per session plus platform fee. Specific pricing not publicly listed; quotes on request. | Bilateral contract required; commercial only. | **40–80 hours** for an OCPI 2.2.1 client + Hubject onboarding test cycle (estimated, not measured). Even then — useless for HK today. | Real-time availability, session start/stop, **but only for networks already on Hubject.** HK operators aren't on it. |
| **EV.energy** — `https://ev.energy/` | **None for HK.** Their public materials say "300k users across US, UK and Europe." No HK presence. | Subscription / utility-program based; not relevant for us. | Commercial. | n/a | Smart-charging orchestration, not a data aggregator we could use. |
| **ChargePoint API (developer.chargepoint.com)** | **Effectively none for HK.** ChargePoint-the-network has a global OCPI endpoint, but the `ChargePoint Developer` portal at `https://developer.chargepoint.com/` (visited 2026-09-14) is login-only and US/EU-focused. CLP's HK chargers run on legacy ChargePoint hardware, but CLP does not publish a partner-facing CP API. | Commercial — developer signup + per-call fees (no public rate card; spec doc exists at `docs.chargepoint.com` and the SOAP WSDL at `webservices.chargepoint.com/cp_api_5.1.wsdl`). | Commercial. | **20–40 hours** for a SOAP/JSON wrapper + onboarding. But no HK data to retrieve. | ChargePoint-network global station list, real-time status. **No HK operators use this surface.** |
| **Chargeprice** (bonus — `chargeprice.app`) — `https://github.com/chargeprice/chargeprice-api-docs` | UK / EU focused. Could provide cross-reference tariff data for trips. | Free for non-commercial; commercial contracts available. | Open-data-ish for basic tiers. | 8–16 hours. | Detailed tariff structures (kWh + per-minute + session fees) for European networks — useful if we ever expand. |

**Verdict on aggregators:** **OCM is the only one that gives us anything for HK today, and even OCM is a strict subset of (a) the EPD XLSX + (b) the CLP public JSON.** The "pay to normalize the data" pitch is real in Europe but does not yet exist for HK. We should not pay for Hubject / ChargePoint / EV.energy access until at least one HK operator is on the other side.

---

## 5. OCPP feasibility

**Is it worth building our own OCPP client?** **No, for an aggregator/wallet; yes, for a CPO.**

- **What OCPP is.** OCPP (Open Charge Point Protocol) is the wire protocol between an EV charge point and a Charging Station Management System (CSMS). It runs over WebSocket (1.6 JSON and 2.0.1+) or SOAP (1.6 only). Maintained by the **Open Charge Alliance** (`https://openchargealliance.org/`), a non-profit founded 2014, 400+ members. Three live versions: **OCPP 1.6** (2015, widely deployed), **OCPP 2.0.1** (2020, IEC 63584 since 2024), **OCPP 2.1** (2025, IEC 63584-210, adds V2X / battery swap / ISO 15118-20 support). Source: `https://openchargealliance.org/protocols/open-charge-point-protocol/` (visited 2026-09-14).
- **Why we don't need one.** OCPP is a CPO-facing protocol — it's how a charge point talks to *its* backend. EV Wallet HK is an **eMSP / wallet**, not a CPO. Our position in the value chain is: pull station/pricing data → display to user → settle payment. None of those roles requires us to terminate OCPP at all. The OCPP traffic that matters is between CLP / HKEV / Shell / Tesla and their own CSMS, behind their own firewalls.
- **What would building it look like.** Even if we decided to build, **OCPP is hard.** A minimal 1.6 central-system client (WebSocket + BootNotification + Heartbeat + StatusNotification + StartTransaction + StopTransaction + MeterValues) using the **`mobilityhouse/ocpp`** library (Python, MIT-licensed, ~1k GitHub stars, `https://github.com/mobilityhouse/ocpp`, visited 2026-09-14) is roughly **40–80 hours** of dev work to get a working client with a fixture CSMS (the library's `examples/v16/` and `examples/v201/` directories give us the CSMS side; the OCA publishes an OCTT test tool at `openchargealliance.org/test-tool/` for conformance). Library alternative: **`ocpp-asgi`** (`https://github.com/villekr/ocpp-asgi`, PyPI `ocpp-asgi 0.4.0`) wraps `mobilityhouse/ocpp` for ASGI / FastAPI / serverless — useful if we did build on top of our existing FastAPI app. Adding OCPP 2.0.1 (transaction-event model + device-model + ISO 15118) at least doubles the work.
- **Integration test setup.** Run two instances: a charge-point simulator (the `mobilityhouse/ocpp` `ChargePoint` class) and a CSMS simulator (`CentralSystem` class). Connect them via local WebSocket. Run through the OCA OCTT conformance suite for the chosen version (1.6 or 2.0.1) before claiming support. Expect ~16 hours of conformance debug.
- **Buy vs build.** We **buy**, in the sense that the CLP public JSON *is* the post-OCPP data we want, after CLP's own CSMS has done the heavy lifting. If we ever needed raw OCPP (e.g. to talk directly to a private fleet's chargers), the buy-side would be a hosted OCPP platform like AMPECO, Monta, or ChargePoint — those are out of scope for an HK aggregator app, but they exist as fallbacks.
- **Bottom line:** spend the engineering hours on the `clp-poll` n8n workflow hardening and the HKEV partnership outreach, not on OCPP.

---

## 6. Recommendation (numbered, time-bucketed)

### This week (before 2026-09-21)
1. **Ship the CLP JSON integration.** Replace the stub `clp-poll` workflow's hardcoded data with a real `GET https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList` (or the `data.gov.hk` proxy). Map the response fields (`cpStatus`, `chargerType`, `longitude`, `latitude`, `detailedAddress`, `lastUpdate`) into our `ChargingStation` / `Pole` schema. **Auth: requires either Akamai-friendly headers (browser User-Agent) OR a `data.gov.hk` CKAN API key** (register at `https://data.gov.hk/en/help/ckan-api-development-guide`). **Estimated: 6 hours dev + 2 hours QA.**
2. **Ingest the EPD XLSX once.** Run a one-shot n8n workflow that downloads `EV_Charger_Locations_EPD_Web_<latest_quarter>_eng.xlsx` from `epd.gov.hk`, parses it, and seeds the station table for all non-CLP / non-Tesla operators. **Estimated: 8 hours dev (including XLSX parsing in Python).** Schedule quarterly refresh from then on.
3. **Document a manual Tesla / Shell station list** as a JSON file in the repo. Source it from the EPD XLSX (which explicitly tags "Tesla Supercharger" and "Tesla Wall Connector" sites) + the Shell Recharge HK HTML table on `shell.com.hk`. Hand-curate prices (Tesla: dynamic; Shell: HK$3.5/kWh fast). **Estimated: 4 hours.**

### This month (before 2026-10-14)
4. **Email HKEV Power Limited** (`info@hkev.com.hk`, the only public contact we could find) to request a data-share partnership. In parallel, **implement a polite, attributed scrape of `portal.hkev.com.hk`** as a fallback: rate-limit to 1 request / 15 minutes per station, cache aggressively, label the data as "© HKEV Power Limited" in the UI, and stop on any `4xx`/`5xx`. Get a sign-off from legal before going live.
5. **Build the rate-normalization layer** (already partially stubbed in `rate-normalize` n8n workflow per `docs/PROVISIONING.md`). The CLP API does not expose pricing; the EPD XLSX does not expose pricing; HKEV's portal shows pricing. Realistic goal: a "best-known price" field on each pole with a freshness timestamp, honestly labeled.
6. **Add OCM as a *cross-check*, not a primary source.** One weekly job to `GET https://api.openchargemap.io/v3/poi/?countrycode=HK&key=...&opendata=true` (free tier, request an API key first). Diff against our station table and flag stations we are missing. **Estimated: 4 hours.** This catches new operators faster than EPD's quarterly refresh.

### This quarter (before 2026-12-31)
7. **Migrate the CLP URL** off `https://api.clp.com.hk/evcharger/list` (sunsetting 2027-01-01) to `https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList`. Schedule the cutover for **2026-12-15** to leave two weeks of buffer.
8. **Watch-list:** OCPI 2.2.1 adoption by HK operators (none as of 2026-09-14). If CLP / HKEV / Shell ever expose an OCPI endpoint, we should re-evaluate. ChargePoint API / Hubject access remain no-ops for HK until that changes.
9. **Watch-list:** the Tesla Enterprise / "Charging Partners" programme. Revisit only if we have paying users and a real product to demo; expect a "no HK partner program" reply for now.
10. **Legal review.** Before shipping anything that ingests HKEV / Shell / Tesla data, get a one-page legal memo (HK-qualified solicitor) covering: (a) the data.gov.hk Open Data licence for the CLP + EPD datasets, (b) HK Copyright Ordinance §39 / §198 fair-dealing for the HKEV portal, (c) Tesla ToS compliance for any Tesla content we display.

---

## 7. Sources (visited 2026-09-14, HKT)

### Operator pages

- Tesla HK Find Us — `https://www.tesla.com/en_hk/findus` (landing; no public API surface found)
- Tesla HK Legal — `https://www.tesla.com/en_hk/about/legal` (lists "Terms of Use" and "Supercharger Fair Use Policy")
- Tesla HK Terms of Use — `https://www.tesla.com/en_hk/legal/terms` (Supercharger Fair Use Policy text quoted in §3.1; the policy states "we may also take additional action to protect the availability of Superchargers… such as limiting or blocking your vehicle's ability to use Supercharger stations")
- HKEV E-Charge HK portal — `https://portal.hkev.com.hk/` (HTML rendered with 191 stations, 1377/2559 chargers available at fetch time; pricing per-kWh and per-hour visible)
- HKEV corporate — `https://www.hkev.com.hk/` (Chinese-language site; Firecrawl scrape timed out; corporate site intermittently unavailable)
- HKEV announcements — `https://www.hkev.com.hk/echarge_main/echargeinfo?lang=en_HK&type=update` (page title in search index; could not load content in time — `Inferred from search result description only`)
- Shell Recharge HK — `https://www.shell.com.hk/en_hk/motorists/shell-recharge.html` (HTML station table; ~40 sites listed; pricing not in table)
- CLP eMobility — `https://www.clp.com.hk/en/emobility/about-electric-vehicles/locations` (consumer-facing list; served from same backend as the public API)
- CLP About EV — `https://www.clp.com.hk/en/emobility/about-electric-vehicles` (background; references the 2025-03-18 fee-paying transition PDF at `clp.com.hk/content/dam/clphk/documents/20250318_EN.pdf`)
- CLP public JSON — `https://api.clp.com.hk/evcharger/list` (returns HTTP 403 from a bare curl; payload is served via the `data.gov.hk` mirror)
- CLP new JSON URL — `https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList` (per CLP / `data.gov.hk` notice; will retire 2027-01-01)

### Open Data and aggregators

- DATA.GOV.HK — CLP dataset — `https://data.gov.hk/en-data/dataset/clp-team1-electric-vehicle-charging-stations` (dataset description; "API Available" badge; URL change notice)
- DATA.GOV.HK mirror payload (visited via curl on 2026-09-14) — `https://res.data.gov.hk/api/get-download-file?name=https%3A%2F%2Fapi.clp.com.hk%2Fevcharger%2Flist` (returned 25 stations in JSON, first record Cheung Fat Plaza)
- EPD EV Charger Reference Database — `https://www.epd.gov.hk/epd/english/environmentinhk/air/promotion_ev/locations_ev_chargers.html` (quarterly XLSX download list; 2025-Q3 + 2026-Q1 available at fetch time)
- EPD XLSX (sample row) — `https://www.epd.gov.hk/epd/sites/default/files/epd/english/environmentinhk/air/promotion_ev/files/EV_Charger_Locations_EPD_Web_20250331_eng.xlsx` (data preview showed: 81 BS1363 + 1497 IEC 62196 standard; "Tesla Supercharger" / "Tesla Wall Connector" tagged in remarks)
- Hubject — `https://www.hubject.com/` (75+ countries, 1.1M+ charge points; no HK operators listed)
- Open Charge Map — `https://openchargemap.org/` and `https://openchargemap.org/site/develop` (referenced in their `ocm-docs` repo)
- Open Charge Map Developer Guide — `https://github.com/openchargemap/ocm-docs/blob/master/System/DeveloperGuide.md` (10-year-old doc; still the canonical guide)
- Open Charge Map API endpoint — `https://api.openchargemap.io/v3/poi/?countrycode=HK&maxresults=5&compact=true` (returned HTTP 403 + body "You must specify an API key using the key query parameter or x-api-key header.")
- Open Charge Map OCM-Client (Node) — `https://www.npmjs.com/package/@cardog/ocm-client` (community wrapper, all v4 endpoints covered)
- ChargePoint Developer Portal — `https://developer.chargepoint.com/` (login wall; couldn't verify coverage details)
- ChargePoint SOAP WSDL — `https://webservices.chargepoint.com/cp_api_5.1.wsdl` (referenced from `apis.io`)
- ChargePoint Products (marketing) — `https://www.chargepoint.com/products/software` (claims "open API and more than 40 integrations")
- EV.energy — `https://ev.energy/` (300k users; US/UK/EU; no HK)
- EV.energy platform (B2B) — `https://platform.ev.energy/` (orchestration platform; not relevant to a wallet app)

### Protocols and libraries

- Open Charge Alliance — `https://www.openchargealliance.org/` (OCA; OCPP stewardship; IEC 63584 approved)
- Open Charge Alliance OCPP page — `https://openchargealliance.org/protocols/open-charge-point-protocol/` (1.6 / 2.0.1 / 2.1 versions; ISO 15118-20 in 2.1)
- mobilityhouse/ocpp (Python OCPP library) — `https://github.com/mobilityhouse/ocpp` (1k stars; MIT; last commit 2026-07-19; v2.1.0 release 2025-07-16)
- ocpp-asgi (Python ASGI wrapper) — `https://github.com/villekr/ocpp-asgi` (extends mobilityhouse/ocpp; v0.4.0 on PyPI)
- OCPI protocol repo — `https://github.com/ocpi/ocpi` (OCPI 2.1.1, 2.2.1, 2.3.0 versions maintained by EVRoaming Foundation)
- OCPI overview — `https://ocpi-protocol.com/` (run by the EVRoaming Foundation)

### Supporting context

- MoneySmart HK operator overview — `https://blog.moneysmart.hk/en/living/hong-kong-ev-charging-station-location-map-tesla-clp-power-hong-kong-electric-link-reit-shell/` (operator landscape, no API info)
- Wilson Parking × Cornerstone Technologies announcement — `https://en.prnasia.com/releases/apac/cornerstone-technologies-provides-ev-charging-solution-for-wilson-parking-353336.shtml` (2022; Wilson Parking Admiralty Car Park)
- Wilson Group HK (LinkedIn) — `https://www.linkedin.com/posts/wilsongrouphk_wilson-parking-set-up-the-first-ev-charge-activity-6883703515043237888-jTYa` (13 quick+medium chargers at Admiralty Car Park)
- Computime enters HK EV market — `https://www.evcandi.com/news/computime-steps-hong-kongs-ev-charger-market` (mentions HK CPO partners)
- Chargeprice API docs (bonus aggregator) — `https://github.com/chargeprice/chargeprice-api-docs` (43 stars; EU tariff data)

### Speculation flagged as such

- §3.2 — HKEV's pricing model heterogeneity (per-kWh vs per-30-min vs per-60-min) is **observed from the live portal at fetch time 2026-09-14**, not from a written statement.
- §3.2 — HKEV OCPP at integrator tier: "Unknown" — **inferred from absence of any published OCPP CSMS**, not from a statement by HKEV.
- §3.4 — CLP's transition to fee-paying was on **18 March 2025**, per the CLP PDF notice referenced on their consumer page (confirmed via the EPD XLSX 2025-Q1 onwards). **Inferred from CLP's own published notice.**
- §3.4 — CLP OCPP status: "Unknown — CLP does not advertise OCPP at the integrator tier" — **inferred from absence of any published partner-facing CSMS.**
- §4 — Hubject pricing "~€0.10–0.30 per session plus platform fee": **inferred from public industry write-ups, not from a Hubject rate card.** Their actual quote is per-engagement.
- §5 — OCPP integration estimates (40–80 hours, 16 hours conformance) are **inferred from the size of the spec + library maturity**, not measured; sanity-check with a spike before committing.

---

## Appendix A — Field-mapping cheat sheet

For the `clp-poll` n8n workflow writing into our `ChargingStation` / `Pole` schema (see `src/evwallet/internal/router.py` for the canonical Pydantic models):

| CLP JSON field | Schema field | Notes |
|---|---|---|
| `geoLocationResult[].title` | `StationUpsertIn.name` | Station name as CLP publishes it (e.g. "Cheung Fat Plaza"). |
| `geoLocationResult[].detailedAddress` | `StationUpsertIn.address` | Free-text address; needs district derivation. |
| `geoLocationResult[].longitude` / `.latitude` | `StationUpsertIn.longitude` / `.latitude` | Decimal degrees. |
| `geoLocationResult[].itemId` | `StationUpsertIn.external_id` | Stable integer per station. |
| `geoLocationResult[].chargerList[].cpNo` | `PoleUpsertIn.external_id` | String per pole; CLP types these as strings even though they're numeric. |
| `chargerList[].chargerType` ("Quick" / "Semi-Quick") | `PoleUpsertIn.connector` + `speed_tier` + `max_kw` | **Needs inference.** CLP doesn't expose max kW. Suggested mapping: "Semi-Quick" → connector=`iec62196_t2`, speed_tier=`medium`, max_kw=22; "Quick" → connector=`ccs_combo_2`, speed_tier=`fast`, max_kw=50. Mark as `inferred` in `raw_payload`. |
| `chargerList[].cpStatus` | `PoleUpsertIn.status` | Mapping: `Status_Not_Available` → `available` (CLP uses this for "not currently in use, not reporting issues" — see the very high counts); `Available` → `available`; `Occupied` → `occupied`; `Out_Of_Service` → `out_of_service`. **Verify the semantic mapping with a 10-minute spot check before trusting it in production.** |
| `lastUpdate` | `PoleUpsertIn.status_updated_at` | Format `YYYY-MM-DD HH:MM:SS`; convert to UTC. |
| `provider` ("CLP") | `StationUpsertIn.provider_code` | Hardcode as `"clp"`. |
| `*` (everything else) | `StationUpsertIn.raw_payload` | Dump the whole CLP object; downstream debugging becomes cheap. |
| **Not exposed:** pricing, parking fees, peak/off-peak tariff, hours of operation | — | CLP API has none of these. We must hand-curate from `clp.com.hk` consumer pages or derive from historical session data we don't have. |