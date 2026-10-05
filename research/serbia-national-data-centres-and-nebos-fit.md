# Serbia's National Data Centres, and Where Nebos Fits

*Compiled 5 October 2026. This follows on from [`mihailo-jovanovic-ite-serbia-ai.md`](./mihailo-jovanovic-ite-serbia-ai.md). Facts come from search-result summaries, because direct page fetches were blocked. Check anything marked **[UNCONFIRMED]**, and any figure you plan to quote, against its source before presenting it.*

---

## Part A: The state data-centre estate

### A1. Who runs it

| Body | What it does | Key facts |
|---|---|---|
| **ITE (Office for IT and eGovernment)** | Owns and runs the state data centres, the State Cloud, eUprava, and the NOC/SOC. Interim cyber authority. | About 90 staff and about RSD 1.8bn in spending ([Danas](https://www.danas.rs/vesti/drustvo/kancelarija-za-it-i-eupravu-ulaze-oko-18-milijardi-dinara-u-razvoj-elektronske-uprave-i-it-sektora/)). Has a dedicated State Cloud group in its IT Infrastructure Sector ([ITE](https://www.ite.gov.rs/tekst/sr/1828/sektor-za-it-infrastrukturu.php)). |
| **Data Cloud Technology (DCT)** | 100% state-owned commercial arm of the Kragujevac site, set up Dec 2020. Sells colocation, IaaS and cybersecurity as a service. | CEO **Danilo Savić**. **40 staff.** 2024 income RSD 405.5m (about €3.5m), net profit RSD 9.7m ([CompanyWall](https://www.companywall.rs/firma/data-cloud-technology/MMxEdRl0Y), [DCT](https://dct.rs/en/about-us/company.html)). Vučić claimed €6m/yr tenant revenue, heading to €10m ([Pink](https://www.pink.rs/politika/781884/ovo-nema-niko-u-regionu-vucic-obisao-data-centar-u-kragujevcu-uzimamo-sest-miliona-evra-godisnje-od-zakupaca-verujemo-da-ce-biti-10)) **[does not match DCT's books]**. |

### A2. The sites

| Site | Status | Capacity | Notes |
|---|---|---|---|
| **Kragujevac State DC (primary)** | Live since Dec 2020. Modules 3–4 (+8 MW) commissioned **30 Apr 2026**. | **14 MW**, 1,080 racks, 14,000 m² | EN 50600 Class 4, re-certified Apr 2026. 2N redundancy, 96 h of generator fuel. **More than €100m invested so far**, against €40m planned ([Danas](https://www.danas.rs/vesti/ekonomija/pusten-u-rad-novi-superkompjuter-u-data-centru-u-kragujevcu-do-sada-ulozeno-100-miliona-evra/), [ai.gov.rs](https://www.ai.gov.rs/vest/sr/2466/srbija-unapredjuje-digitalnu-infrastrukturu-pusteni-u-rad-novi-kapaciteti-data-centra-u-kragujevcu.php)). Tenants: **Oracle (OCI "Jovanovac" cloud region), IBM, Huawei, Honeywell, CETIN, Telekom Srbija**. |
| Kragujevac "Block 2" | Designed | about 40–56 MW | e& (UAE) MoU, Sep 2025, for +40 MW. No contract or capex figure since ([DCD](https://www.datacenterdynamics.com/en/news/serbia-signs-mou-with-e-enterprise-to-triple-national-data-center-capacity/)). |
| **Belgrade State DC (backup)** | Live since Dec 2017, in Telekom's "TK Centar" building | 640 racks, "Tier 3+" (self-declared) | Built with Telekom Srbija (about €9m) ([DCD](https://www.datacenterdynamics.com/en/news/prime-minister-opens-serbias-first-state-data-center/)). Now the disaster-recovery and secondary site. |
| **Niš (Niška Banja)** | **Stalled.** The land transfer was pulled from the city assembly about 10 Sep 2026 after protests about water and the spa. | 20 MW, about €50m | ([Južne vesti](https://www.juznevesti.com/drustvo/studenti-i-brojni-gradjani-protiv-data-centra-u-nisu-strahuju-za-vodu/), [srpske.rs](https://srpske.rs/vesti/ekonomija/2026/09/06/data-centar-kod-niske-banje-20-megavata-a-toplota-bi-grejala)) |
| **Novi Sad** | Early planning. Draft location; meeting with the mayor in Mar 2026. | 5 MW to start, €50–60m | ([B92](https://www.b92.net/lokal/novi-sad/ekonomija/232717/drzavni-data-centar-i-u-novom-sadu-najavljen-veliki-tehnoloski-projekat-za-vojvodinu/vest)) |
| **Target** | 2030 / 2035 | **more than 200 MW / about 1 GW** | ([eKapija](https://www.ekapija.com/news/5467593/planira-se-prosirenje-kapaciteta-drzavnih-data-centara-na-200-mw-superkompjuteri-osnova)) |

**Scale check:** all commercial data centres in Serbia combined come to only about 27 MW ([Baxtel](https://baxtel.com/data-center/serbia)). The state is by far the largest operator, and its 200 MW goal is roughly 7 times today's whole market.

### A3. What runs inside
- **State Cloud** ([ITE](https://www.ite.gov.rs/tekst/sr/9654/drzavni-klaud.php), [World Bank, Feb 2026](https://www.worldbank.org/en/news/opinion/2026/02/25/serbia-s-quiet-digital-transformation)):
  - VMware infrastructure-as-a-service, split across Kragujevac and Belgrade
  - ISO 27001, 20000 and 9001
  - **about 80 public bodies, more than 420 systems, more than 2,500 virtual machines**
- **Other services:** hybrid cloud, telehousing, and a **NOC and SOC**. The SOC is World Bank-funded and handles **about 1 billion security events a day**.
- **State Oracle Cloud agreement:** described as the second in Europe after the UK.
- **Notable data held:** EPS (electricity), EDS (distribution), and eUprava, which has 3 million users.
- **Supercomputers:**
  - DGX A100: 5 PF, free to about 80 startups and academic users
  - DGX H200: 32 PF, live 30 Apr 2026
  - **Eviden system: €50m, now reported as 640 NVIDIA Grace Hopper chips arriving Q1 2027**, paid for by a **€42.5m French Treasury loan** with at least 50% French content, in a new water-cooled hall ([Bloomberg Adria](https://rs.bloombergadria.com/tehnologija/digitalizacija/105087/superkompjuter-u-kragujevcu/news), [Nova](https://nova.rs/vesti/biznis/srbija-uzima-novi-kredit-od-francuske))
- **Mandate to host there:** no law requires state bodies to use the state DC. In practice, the pressure is political: in Oct 2025 Vučić told institutions they would be "arrested" if they lost data that wasn't held in the state DC ([Danas](https://www.danas.rs/vesti/ekonomija/vucic-zaposlenima-u-data-centru-euprave-bicete-pohapseni-ako-dodje-do-sajber-napada/)).

### A4. Cybersecurity context (a major live pain point)
- **Law on Information Security**, adopted 22 Oct 2025:
  - creates a new **Office for Information Security** that absorbs the National CERT
  - larger operators must set up their own CERTs
  - ITE covers these duties until the Office starts, with the start date reported as **1 Jan 2027** ([Tanjug](https://www.tanjug.rs/ekonomija/srbija/253067/mihailo-jovanovic-kancelarija-za-informacionu-bezbednost-pocinje-sa-radom-1-januara/vest)) **[some sources say 2026]**
- **Major incidents:**

| Date | Target | What happened |
|---|---|---|
| 2022 | RGZ cadastre | Ransomware; systems out for weeks |
| Dec 2023 | EPS | Qilin ransomware; 34 GB leaked |
| Jul 2025 | Ministry of Justice data centre | A week of disruption ([RFE](https://www.slobodnaevropa.org/a/ministarstvo-pravde-srbije-hakerski-napad/33471783.html)) |
| Mar 2026 | Telekom Srbija | 150k–600k customer records stolen |
| Mar 2026 | APR business register | Data leak |
| Aug 2026 | RFZO, bailiffs, MUP and others | "INF Grupa" arrested for attacks on these bodies |

- **Officially claimed attack volumes:** 2.6 million attempts against the DC in Mar 2026 ([B92](https://www.b92.net/english/business-economy/229435/vucic-eur100-million-invested-in-a-data-center-26-mil-attempted-hacker-attacks-on-serbias-infrastructure/vest)).
- **Innovation District:** its first building (Kragujevac, more than €20m) houses the **National Information Security Centre**, with Oracle, Honeywell and Palo Alto Networks as tenants. Opening was due mid-Sep 2026 **[UNCONFIRMED whether it happened]**.

### A5. Weak points (what critics and the numbers show)
1. **No published PUE, energy or occupancy figures.**
   - Academic estimates: 14 MW is about 123 GWh/yr, around 25% of Kragujevac's consumption. At 56 MW it would be roughly the whole city ([Nova ekonomija](https://novaekonomija.rs/vesti-iz-zemlje/data-centar-u-kragujevcu-bi-mogao-da-trosi-struje-kao-ceo-taj-grad)).
   - Promised mitigations, both still mostly unbuilt: rooftop solar of 300–500 kW, and waste heat for district heating.
2. **Social licence:**
   - The Kragujevac site is 100–150 m from the Jovanovac landfill, which has had methane and fires. This contradicts ITE's own siting criteria ([Radar](https://radar.nova.rs/ekonomija/drzavni-data-centar-u-kragujevcu-ai/)).
   - Niš was stopped by protests over water.
3. **Cost overruns and transparency:**
   - Kragujevac went from €40m planned to more than €100m.
   - Niš councillors said they had "not even minimal information".
   - Media questioned whether the €50m supercomputer will be used ([Raskrikavanje](https://www.raskrikavanje.rs/page.php?id=Nemamo-jedini-besplatan-superkompjuter-na-svetu-niti-je-on-jedini-u-regionu-1268)).
4. **Trust in state data:** repeated leaks with no accountability, plus 2026 state-linked spyware cases against students ([BIRN](https://balkaninsight.com/2026/09/07/serbias-spyware-abuse-escalates-as-students-targeted-in-election-run-up/bi/)).
5. **Vendor concentration:** VMware (Broadcom pricing risk, my inference), the Oracle agreement, Huawei kit bought with a Chinese grant, and a French-tied loan.
6. **Thin operating team:** DCT has 40 people and ITE about 90, running about 80 public bodies, 420 systems, 3 supercomputers and a planned 14-fold growth in capacity.

### A6. Competitive and market context
- **Telekom Srbija:** about 4 MW in Belgrade.
- **CETIN:** 3.5 MW, plus 3.6 MW planned at Beli Potok.
- **Orion:** new Belgrade DC.
- **Hyperscalers:** Oracle is the only one present, and it sits inside the state DC. No Microsoft, Google or AWS regions.
- **Dimitrovgrad:** a 320 MW, €2bn project exists only as a letter of intent; the investor is unknown.
- **DCT is exporting the model:** a DCT-led consortium won a feasibility study for a Republika Srpska state data centre (May 2026, [Detektor](https://detektor.ba/2026/05/14/republika-srpska-planira-izgadnju-data-centra-po-uzoru-na-srbiju/)).

---

## Part B: What Nebos is, seen through ITE's eyes

*Taken from Nebos's own positioning material. The live Nebbos connector rejected its token, so I couldn't check current product status there.*

| Nebos capability | Why ITE might care | Friction |
|---|---|---|
| **AI operations brain above existing systems** (connects through MCP, migrates nothing) | ITE runs a stack from many vendors: VMware, Oracle, Huawei, NOC/SOC tools, eUprava, eBolovanje. It can't rip and replace any of it. | We don't know which tools ITE actually uses (monitoring, ITSM, DCIM, SIEM). **This is the #1 thing to find out.** |
| **Pearl per department + Orchestrator** spotting problems across departments | ITE plus DCT plus about 80 tenant bodies is a multi-department organisation with a thin team. The Orchestrator could flag bottlenecks such as tenant onboarding, capacity or incidents before they escalate. | Nebos is built for 20–500-person companies. ITE plus DCT (about 130 people) fits that size, but as a **government buyer**, with public procurement. |
| **Institutional memory** ("the company that never forgets") | A 40-person DCT team runs Tier-IV-class operations and is about to grow 14-fold. Losing key people is a real risk to Kragujevac operations. Lessons from the 2025–26 incidents need to be kept. | Needs a good proof story, e.g. the TR3I dogfooding numbers. |
| **Append-only audit trail, human approval required, no surveillance** | Lines up with the new Information Security Law and its own-CERT requirements. Answers the public trust deficit. | The audit trail would need to be shown to meet Serbian law and the EU AI Act. |
| **Sovereign export** (in the patent claims) and strict per-customer data isolation | Fits Jovanović's core line: "data stays in Serbia". | **Biggest gap.** Nebos currently routes to Anthropic models and appears to be hosted on Railway, a US cloud. ITE will expect it to run on Kragujevac infrastructure, ideally with models served locally: Mistral, the future Serbian LLM, or open models on the H200 or Eviden systems. |
| **Serbian R&D team** (70% R&D salary exemption, IP Box) | A domestic company: "Serbian AI built in Serbia" fits ITE's AI-hub story, the DAR Sandbox and the science parks. | — |
| **ADGM (Abu Dhabi) investor entity** | Matches the Gulf ties already in ITE's plans (e& MoU, G42 MoU, UAE AI MoU). | Make sure this reads as a strength, not as data leaving Serbia. |

---

## Part C: What to find out before presenting (priority order)

1. **Their actual operations tooling.** Which monitoring, ITSM, DCIM/BMS and SIEM products do ITE and DCT use, and do they have MCP servers or APIs? I could find nothing public. Ask, or screen [jnportal.ujn.gov.rs](https://jnportal.ujn.gov.rs) for ITE and DCT tenders.
2. **Where the operational pain is.**
   - **Capacity growth:** 14 to 200 MW with the same team.
   - **Tenant onboarding:** about 80 public bodies today, and more under political pressure to migrate.
   - **Incident response and CERT duties** under the new law.
   - **Energy/PUE reporting**, which critics are demanding.
3. **The sovereign-deployment question.** Could Nebos run fully in the State Cloud or on DCT colocation, with models served locally on the H200 now, or on Mistral through the Eviden factory from 2027? This needs an engineering answer before the meeting.
4. **Procurement route.** Options to check:
   - the **"AI District" public call** run with UNDP (closed 30 May 2026; ask whether there is a second round)
   - a **DAR Sandbox** pilot
   - **SAIFA** services, which are EU-funded
   - a DCT commercial partnership, where Nebos is sold to DCT's tenants as a managed service
   - a direct ITE pilot
5. **Who actually decides.** Jovanović sets the vision. Danilo Savić (DCT) owns data-centre operations and revenue. The ITE IT Infrastructure Sector owns the State Cloud. A pilot sponsor is more likely to be Savić or that sector than Jovanović.
6. **Dates to watch:**
   - Information Security Office start (Jan 2027)
   - Eviden system arrival (Q1 2027)
   - Expo 2027
   - a possible second round of the AI District call

## Part D: Angles to present (my synthesis)

1. **"Operations intelligence for the sovereign cloud."** Nebos as the brain for ITE and DCT's own operations: capacity, tenant onboarding, incidents, energy reporting. It sits above VMware, Oracle and the NOC/SOC tools without replacing them, and it runs in Kragujevac.
2. **Answer the transparency criticism.** Automatic, auditable reporting on PUE, energy, occupancy and incident lessons. This turns the critics' main charge ("no figures published") into a strength for ITE.
3. **CERT-readiness for state bodies.** Under the new law, many bodies need incident processes they don't have. Nebos Pearls hold the playbooks and the incident memory, and DCT resells this as a managed service alongside its cybersecurity offering.
4. **A reference customer for "Serbian AI on Serbian supercomputers".** Nebos running its model tier on the national AI platform gives Jovanović a showcase of a domestic company using the infrastructure. That answers the "who will use the supercomputer?" criticism.
5. **Start small:** one 90-day pilot inside DCT's 40-person operation, entered through the DAR Sandbox or the AI District call, with metrics he can announce. He announces metrics constantly.

**Avoid:**
- the per-seat ROI and Salesforce comparisons (irrelevant to a government buyer)
- anything that sounds like surveillance of civil servants
- any architecture that sends data or prompts outside Serbia
