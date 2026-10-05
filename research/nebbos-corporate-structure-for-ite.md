# Nebbos Group Structure: How to Present It to Serbia's Office for IT and eGovernment

*Prepared 5 October 2026 for the ITE meeting (Dr Mihailo Jovanović, Director). This builds on [`mihailo-jovanovic-ite-serbia-ai.md`](./mihailo-jovanovic-ite-serbia-ai.md) and [`serbia-national-data-centres-and-nebos-fit.md`](./serbia-national-data-centres-and-nebos-fit.md).*

> **Confirm before use:** registered legal names and spelling ("Nebbos" vs "Nebos"), company registration numbers, ownership percentages, and which entity holds the IP today. Section 6 lists conflicts with our existing investor material that must be resolved first.

---

## 1. Who we're presenting to, and what they will judge

**Audience:**
- Jovanović is a government director and a former minister.
- His title is Assistant Professor; he holds two PhDs and is trained as an electrical engineer.
- He reports to the Prime Minister and runs Serbia's state data centres, the State Cloud, the eGovernment portal (eUprava) and the national AI platform.

**What he will listen for:**

| His priority | What it means for how we present the structure |
|---|---|
| **Digital sovereignty:** "data stays in Serbia, Serbia builds its own AI" | Lead with the **Serbian entities and Serbian IP**. The US entities support the business; they are not where the product comes from. |
| **Serbia as a regional AI hub**, and record ICT exports (€4.55bn in 2025) | We are a domestic AI company that sells globally, which is exactly the success story he announces. |
| **Accountability and security**, after the 2025–26 hacks and the new Information Security Law | One clearly named Serbian contracting entity, with local staff, local support, and a clear liability chain. |
| **Public procurement and audit** | Every entity should be explainable in one sentence. No grey areas around IP, data or ownership. |

**The rule for this audience:** the corporate structure is **not a sales slide**, it is a **trust slide**. One slide, shown late, that answers the questions his lawyers and procurement team will ask anyway. Don't open with it. Per our deck rules, more than 2 minutes on "about us" hurts outcomes.

---

## 2. The structure (as briefed)

```
                    ┌───────────────────────────────────┐
                    │   NEBBOS TECHNOLOGIES (Delaware)  │
                    │   Group holding company           │
                    └──────────────┬────────────────────┘
               ┌───────────────────┼──────────────────────────┐
               ▼                   ▼                          ▼
   ┌──────────────────┐ ┌──────────────────────────┐ ┌──────────────────────┐
   │ NEBBOS.AI (US)   │ │ NEBBOS TECHNOLOGIES      │ │ TR3I d.o.o. (Serbia) │
   │ Commercial /     │ │ (Serbia)                 │ │ Where the product    │
   │ retail entity:   │ │ Engineering: develops    │ │ was created; IP      │
   │ sells            │ │ and maintains the        │ │ owner; runs Nebbos   │
   │ subscriptions    │ │ Nebbos code              │ │ on its own operations│
   └──────────────────┘ └──────────────────────────┘ └──────────────────────┘
```
*[CONFIRM: is TR3I a subsidiary of the Delaware holding company, or a sister company under common founder ownership? The diagram assumes a subsidiary. This affects the ownership slide and the US-law exposure discussed in §4.]*

| Entity | Jurisdiction | Role, in one sentence | Why it exists |
|---|---|---|---|
| **Nebbos Technologies** | Delaware, USA | Group holding company. | Standard structure for raising international venture capital. |
| **Nebbos.ai** | USA | Sells Nebbos subscriptions to retail and mid-market customers. | Closeness to the largest software market. |
| **Nebbos Technologies (Serbia)** | Serbia | Engineering company that develops and maintains the Nebbos code. | Belgrade engineering talent, plus Serbia's 70% R&D salary exemption. |
| **TR3I d.o.o.** | Serbia | Where Nebbos was invented and where the IP lives. Also the first operational customer, running Nebbos on a 25-person services business. | IP Box regime; origin and proof of the product. |

---

## 3. How to tell this story to ITE

### The core message
> **"Nebbos is Serbian AI: invented in Serbia, owned in Serbia, built by Serbian engineers. We use a US structure to sell to the world and raise capital, the way the companies Serbia is proud of did."**

That frames the structure around his own goals:
- Serbian IP
- Serbian jobs
- exports
- a domestic company using the national AI infrastructure

### Order of emphasis (reverse of a legal org chart)
1. **TR3I d.o.o.:** the IP and the origin story. Serbian-owned IP is our strongest card.
2. **Nebbos Technologies (Serbia):** the engineering team in Serbia, who will support ITE locally.
3. **Delaware holding and Nebbos.ai:** "how we reach global markets and capital". One line each.

### Precedents he will recognise
Name Serbian-founded companies that used a US or foreign holding company with engineering in Serbia, such as Nordeus, Frame/FishingBooker, or Seven Bridges. **[VERIFY each example before using it.]** This makes the structure feel normal rather than evasive.

### Who signs with the government
For any ITE engagement, **recommend that a Serbian entity is the contracting party.** Choose one, TR3I or Nebbos Technologies (Serbia), and present only that one as "your counterparty". Two Serbian entities with overlapping roles will confuse procurement. **[DECIDE which one, see §6.]**

---

## 4. Questions a government buyer will ask, and the answers we need ready

These sit in the **appendix and leave-behind**, not on the main slides. Have them answered before the meeting.

| Area | Their question | What we need |
|---|---|---|
| **Legal standing** | Who exactly are we contracting with? | Serbian entity's registry extract (APR), company number (MB), tax ID (PIB), and authorised signatory. |
| **Beneficial ownership** | Who ultimately owns and controls you? | Entry in the Serbian beneficial-owner register, plus a simple cap-table summary (founders, investors, any foreign state-linked capital). |
| **IP chain** | Who owns the software we would use, and who licenses it to us? | A clean chain: founder and employee invention assignments → TR3I. Intercompany licences between TR3I, Nebbos Technologies (Serbia), the holding company and Nebbos.ai. The licence grant to ITE. |
| **Patent** | What is protected? | US provisional patent: 8 claim families, 16 claims, including "Knowledge System with Sovereign Export". State the filing entity and the plan for international (PCT) filing. |
| **US law exposure** | Can a US authority compel access to our data through your US parent? (CLOUD Act) | **The question that matters most to a sovereignty buyer.** We need a legal opinion plus a technical answer: deployment in the State Cloud or DCT colocation, operated by the Serbian entity, encryption keys held by ITE, and no access path from the US entities. **[GET COUNSEL]** |
| **Data residency** | Where do our data, prompts and models run? | Today the product appears to run on Anthropic models and Railway, a US cloud. We need a **Serbia-hosted deployment option**: Kragujevac infrastructure, with models served locally (Mistral or open models on the national GPUs), or the Serbian LLM once it exists. |
| **Compliance** | Personal data, information security, AI rules | Mapping to the Serbian Personal Data Protection Law (GDPR-aligned), the Information Security Law 2025 (own CERT duties), and EU AI Act alignment. ISO 27001 status or roadmap. |
| **Continuity** | What if your company fails or is acquired? | Source-code escrow held in Serbia, a data-export guarantee (our "sovereign export" claim, which turns the patent into a buyer protection), and change-of-control clauses. |
| **Financial standing** | Can you deliver? | TR3I services revenue (about €2.4m a year, about €200k a month) as the operating base. Funding status of the group. |
| **People and support** | Who supports us, where, and in what language? | Named Serbian team, Serbian-language support, local SLA, and security vetting of staff with production access. |
| **Procurement route** | How can we buy this? | A pilot through the "AI District" public call (UNDP), the DAR Sandbox, or SAIFA, or a direct ITE or DCT procurement under the Public Procurement Law. **[CHECK domestic-preference rules and below-threshold options with counsel.]** |

---

## 5. Recommended slides (structure section only)

*Format follows the Nebos sales-deck skill: action titles, one idea per slide. This is two main slides plus an appendix, placed after the proof section and before the call to action.*

**[Slide A] Nebbos was invented, is owned, and is built in Serbia, and sells to the world from a US base.**
- *Key message:* this is a Serbian AI company; the US entities are its route to market, not its origin.
- *Visual concept:* a simple map. Belgrade on the left (TR3I d.o.o. holds the IP; Nebbos Technologies Serbia runs engineering). A thin line to the US on the right (Delaware holding; Nebbos.ai sells). Serbia is visually dominant.
- *Body copy:*
  - "IP owned by TR3I d.o.o., Belgrade"
  - "Engineering team: Nebbos Technologies, Serbia"
  - "Global sales and capital: Nebbos.ai / Delaware"
- *Speaker note:* "Everything that makes Nebbos work was invented here and is maintained here. We use a US structure the same way successful Serbian tech companies have: to reach customers and investors abroad. For your office, your counterparty would be our Serbian company."

**[Slide B] For ITE, a Serbian company signs, Serbian infrastructure hosts, and the State controls the keys.**
- *Key message:* the structure is built so the State keeps sovereignty over its data.
- *Visual concept:* three stacked blocks:
  - **Contract:** Serbian entity
  - **Hosting:** State Cloud or Kragujevac
  - **Control:** ITE-held keys, escrow and export
- *Body copy:*
  - "Contracting party: [Serbian entity], Belgrade"
  - "Runs in the State Data Centre, Kragujevac"
  - "Your data never leaves Serbia"
  - "Source-code escrow and full data export"
- *Speaker note:* tie this to his own words on sovereignty and to the national AI platform. Offer to run our model tier on the national GPU infrastructure, which also answers the public "who will use the supercomputer?" criticism. **Only say this once the hosting option is real (§6).**

**[Appendix] The due-diligence pack.** One page per row of the §4 table: registry extracts, ownership, IP chain, compliance mapping, continuity, team and support. This is the leave-behind for his legal and procurement staff.

**What to leave out at this level:**
- per-seat pricing
- the Salesforce comparison and the mid-market ROI maths
- investor-round details (raise size, valuation, Gulf investor language)
- the full legal org chart with share percentages; keep it for the appendix if asked
- anything that implies data or prompts leave Serbia

---

## 6. Resolve before presenting (conflicts and risks)

1. **Conflicting entity story.** Our investor-deck reference material describes a different structure:
   - **Nebos.ai Ltd (ADGM, Abu Dhabi)** as the investment entity that **"holds IP post-transfer"**
   - TR3I D.O.O. as the operating company
   - a **pending Serbia → ADGM IP assignment**

   This briefing describes a Delaware holding company with the IP in TR3I. **Which is current?** If the IP is moving out of Serbia, the "Serbian-owned IP" message is weakened. ITE will check, and an inconsistency with investor documents would damage trust.
2. **Brand and legal-name spelling.** Our repository and decks use "Nebos" / Nebos.ai; this briefing uses "Nebbos". Use the exact registered names everywhere.
3. **Two Serbian entities.** Explain clearly why both Nebbos Technologies (Serbia) and TR3I exist, and pick **one** contracting party for government work. Make sure intercompany development and licence agreements exist, so the IP chain is clean.
4. **US-parent exposure (CLOUD Act).** With a Delaware parent, a sovereignty-minded buyer may see US-law reach. Get a short legal opinion and design the ITE deployment so the Serbian entity operates it alone.
5. **The product's current hosting and models** (Railway, Anthropic) contradict a "data stays in Serbia" pitch. Either have a Serbia-hosted deployment option ready, or present it honestly as a roadmap commitment with dates.
6. **Gulf links (ADGM, investors).** These could be an advantage (they match ITE's e& and G42 relationships) or raise a question about data leaving Serbia. Decide whether to mention them at all.

---

## 7. Open questions for you

1. Exact registered names, registration numbers, and ownership of each entity. Is TR3I owned by the Delaware holding company or held separately?
2. Which entity owns the IP **today**, and is any IP transfer planned (ADGM or Delaware)?
3. Which Serbian entity should contract with ITE?
4. Which entity filed the US provisional patent?
5. Can we commit to a Serbia-hosted, locally-modelled deployment for a pilot, and on what timeline?
6. Are we comfortable mentioning the investor structure (Delaware or ADGM, Gulf investors) to a government audience?
