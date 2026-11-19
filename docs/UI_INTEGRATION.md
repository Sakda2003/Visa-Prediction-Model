# Visa and ABA reader UI integration

> Historical design record: the user-authorized entire-interface redesign supersedes the navy/blue/gold preservation guidance and former editor presentation below. See `UI_REDESIGN.md` and root `DESIGN.md` for the current system. Behavior statements here should be checked against current source.

This is an ordinary extension of the existing Global Eagle Visa workspace. The incumbent navy navigation, blue actions, restrained gold navigation accent, light working surfaces, logo and shell remain the visual authority. This document records the implementation visible in source on 2026-11-18; it does not establish a new visual world or claim an approved design comp.

## Scope and evidence

The source review covered `frontend/src/index.css`, `frontend/tailwind.config.js`, `frontend/src/components/Integration.css`, `EvidenceAssessmentPanel.jsx`, `AbaBankPanel.jsx`, `EvidencePolicyPanel.jsx`, `RequirementScoringFields.jsx`, `FirstRunSetup.jsx`, and the Verification Results, Premium Requirements, App Pages and Settings pages. `ApplicationCreator.jsx` and `RequirementsPage.css` were checked for inherited styling variation. The extracted, scoped design inventory is in root `DESIGN.md`, with component previews and unsupported token metadata in `.impeccable/design.json`.

Source is the authority for this record. The initial eight-image `.impeccable/review` packet predates the latest corrections and is not evidence of their final rendered appearance. This documentation pass did not perform final browser, screenshot, keyboard, screen-reader or responsive QA. A final rendered review remains necessary before describing the integrated UI as visually verified.

## Where the extension lives

| Surface | Integrated behavior |
| --- | --- |
| Application verification results | The canonical evidence assessment appears before the existing document summary and review sections. ABA analysis appears with its corresponding statement document. |
| Requirements management | Evidence scoring profiles sit within the existing requirements page. Requirement editing exposes fixed/dynamic scoring, scorer, alternative grouping, source kind/reference and final-assessment requirement fields. |
| Settings | Internal financial/country rules and model lifecycle management use the incumbent white cards and blue actions. Validation, review, approval, activation and rollback have separate visible states. |
| First local setup | A bounded integration panel collects the first administrator's name, email and password. The form communicates the empty-database/local-installation condition and includes no sample client data or default credentials. |
| Legacy bank-analysis route | `/bank-statement-analysis` redirects to applications; bank evidence is reviewed in application context. |

The shell retains the Global Eagle image, navigation, language control, theme control, account display and sticky header. This extension does not create a separate bank-reader product shell.

## Financial reading and action hierarchy

The bank panel leads with statement strength out of 10, average daily posted balance, ending posted balance and the requirement contribution. Money uses an explicit currency and two fractional digits through `Intl.NumberFormat`; the frontend converts the supplied minor-unit amounts for display. Financial figures and tables use tabular numerals. Transaction descriptions wrap while dates, money and source references remain compact. Wide tables scroll inside their labeled, focusable regions.

The daily balance chart uses a step area and disables chart animation. Axis formatting preserves fractional amounts rather than rounding them to whole thousands. Clicking a day pins its amount and available transaction/source references; clicking a source reference selects the row and source page. The final week is shaded. The USD 4,000/5,000 reference lines appear only for USD accounts; they are chart context, not a claim of an official visa requirement. The panel states that posted balances can exclude pending withdrawals and holds.

Transaction search has its own full-width field inside a growing label; it is distinct from the compact source-page number control. Filtering resets transaction pagination. The table presents 20 rows per page, an empty-result message and named previous/next controls.

## Keep the measures distinct

Confirmed evidence strength is the first disclosure, out of 100, with its policy version, state and any completion range. Estimated visa approval probability is a separate adjacent disclosure. Its copy explicitly says that 78/100 evidence points does not mean a 78% approval chance. Unavailable and stale predictions have text explanations. A potential completion upper bound is not a prediction. Model estimates are decision support, not embassy decisions.

The evidence ledger exposes requirement slot, scoring mode, quality, confirmed points, maximum and state/reason. Missing, unscorable or unverified evidence earns no confirmed points; a computed quality alone does not confirm evidence. The bank panel exposes the versioned mapping `quality = (final statement score - 1) / 9` and, where a requirement is available, `minimum + (maximum - minimum) × quality`. It displays the actual confirmed contribution separately. These are application scoring rules, not official policy assertions.

Fixed slots award their allocation after verification. Dynamic manual scoring uses a versioned rubric; alternative requirements share one budget. The profile interface displays an allocated total out of 100, separates draft saving from publication and disables publication for unsaved changes or an invalid budget. Published versions are immutable in the interface. Source fields distinguish internal agency policy from an officially sourced rule and retain a source URL/reference; choosing a source kind does not itself establish that a rule is official.

## Source review and record history

Source review controls are rendered for `canManage`. Source pages are requested through the authenticated API as blobs. The preview is a labeled, keyboard-focusable scroll region, with 100/150/200/300/400% zoom and an option to open the retrieved image at full size. Zoom is applied to the image within the protected preview; it does not convert a protected server URL to a public asset. Selected rows can be highlighted in the requested source page.

The account relationship is loaded from the current analysis record. Explicit ownership confirmation resets on analysis revision/relationship changes, document changes and source-row selection. Confirmation requires a relationship, a reason, reconciled arithmetic and a non-provisional result. The source-resolution checkbox does not waive arithmetic failures. Confirming bank evidence, retaining review status, selecting primary evidence and saving corrections remain separate actions.

Assessment history is read-only and uses Asia/Phnom_Penh timestamps. It records the reviewer, reason, policy references, totals and requirement comparisons. A saved previous assessment, a reconstruction using the previous policy and current evidence, and unavailable prior evidence are explicitly distinguished. Reconstructed figures do not establish historical evidence, and an unknown previous result is not presented as zero. The zero-entry state is explicit.

Reassessment requires an explicit published policy choice and reason and explains prediction invalidation. Current and historical receipt PDF buttons both request authorized blobs through the authenticated receipt endpoint. Historical receipt identifiers and timestamps remain visible; they are not silently replaced by current results. This is a UI/source observation, not an independent backend authorization audit.

## States, semantics and responsive behavior

Errors use text and `role="alert"`; completed mutation notices use `role="status"`. Warning, success and error containers have distinct tones. Status chips and ledger reasons carry words so color is not the sole indicator. Loading, missing analysis, empty search, no policy configuration, unavailable prediction, stale prediction, provisional bank scoring and unresolved reconciliation each have specific text. Busy and invalid actions use native disabled controls. JSON/rubric fields remain labeled disclosures for staff configuration.

Integration fields, selects, textareas and buttons use a visible three-pixel blue focus outline. Native labels, forms, tables, details/summary disclosures, accessible region names and named icon-only pagination controls are present in source. These observations do not certify focus order or assistive-technology behavior without runtime checks.

At 900px and below, bank metrics become two columns, the source/review layout stacks and requirement fields become two columns. At 600px and below, integration forms, the score/probability disclosure and requirement fields become one column; panel padding and heading size decrease. Source images retain their independent zoom/scroll behavior. The incumbent shell uses an overlay navigation on smaller screens and a desktop sidebar, with compact navigation supported. Global rules apply 44px control minimum heights under 1024px and constrain dialogs to the viewport. Requirements management has its own existing responsive rules at 1050px/700px. These are implemented breakpoint rules, not a record of verified device captures.

## Preserved variation and limits

The incumbent is not a single fully consolidated token system. `RequirementsPage.css` defines a scoped navy/blue palette and violet/teal metric accents, while `ApplicationCreator.jsx` already uses violet checklist sections. The Verification Results page also retains some indigo readiness treatment. These are inherited variations, not approval to propagate violet or replace the Global Eagle navy/blue shell. This pass records them without a broad visual repair.

The dark theme is an existing global override rather than a complete set of integration-specific semantic tokens. It needs rendered review on the integration panels before claiming theme parity. English/Khmer shell support is inherited; new integration copy contains English strings and is not evidence of complete localization.

Future UI work should preserve the existing shell, financial legibility, explicit scoring units, source traceability and distinction between evidence and probability. Do not introduce fabricated official requirements, default approval chances, synthetic client records, authentication claims unsupported by the server, or a new brand direction through this documentation.


## Final verification addendum

The documenter produced this source inventory before reaching its usage limit; the primary agent completed the sidecar and documentation check locally. Subsequent desktop, mobile and actual-width captures verified the six listed integration fixes. The independent finish reviewer returned `ship` for those fixes, including protected historical receipt downloads; this is not a whole-application certification. Actual browser download and HTTP SHA-256 checks confirmed that the previous PDF remained unchanged after receipt version 3.
