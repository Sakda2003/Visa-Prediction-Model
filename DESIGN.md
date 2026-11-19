---
name: "Global Eagle Visa workspace"
description: "Source-extracted teal and paper-white staff workspace."
colors:
  navigation: "#103f45"
  primary: "#126d68"
  primary-deep: "#0e5753"
  primary-soft: "#eaf3f0"
  canvas: "#f4f7f6"
  surface: "#fff"
  ink: "#172f33"
  muted: "#52676c"
  line: "#dbe5e3"
  focus: "#64a99f"
  navigation-hover: "#194d52"
  navigation-active: "#2a5a5d"
  navigation-text: "#d6e6e3"
  dark-canvas: "#102024"
  dark-surface: "#172c30"
  dark-ink: "#ecf5f3"
  dark-muted: "#b5cac7"
  dark-line: "#345054"
  dark-soft: "#203d40"
  dark-accent: "#8cd2c7"
typography:
  headline:
    fontFamily: "Manrope, 'Segoe UI', sans-serif"
    fontSize: "28px"
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Manrope, 'Segoe UI', sans-serif"
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Manrope, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Manrope, 'Segoe UI', sans-serif"
    fontSize: "13px"
    fontWeight: 600
  table:
    fontFamily: "Manrope, 'Segoe UI', sans-serif"
    fontSize: "13px"
    lineHeight: 1.65
rounded:
  tag: "5px"
  control: "6px"
  panel: "10px"
  login: "12px"
spacing:
  compact: "8px"
  control: "10px"
  action-gap: "12px"
  group: "16px"
  section: "24px"
  page-top: "32px"
  page-inline: "36px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "{spacing.control}"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "{spacing.section}"
  navigation-active:
    backgroundColor: "{colors.navigation-active}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
---

# Design System: Global Eagle Visa workspace

## Overview

The implemented workspace uses deep teal navigation, white working panels and one Manrope family. This is a source-extracted record of the entire-interface replacement authorized by the user on 2026-11-18. The locally available ABA reader supplied the initial visual benchmark; the user has not identified a different prior project. No named creative metaphor or additional brand story has been selected.

The interface prioritizes agency work: application entry, requirement points, document review and evidence assessment. Labels and values carry the hierarchy; borders divide sections without decorative metric icons, promotional artwork or nested ornamental cards. Routes, authentication and scoring behavior remain product constraints in PRODUCT.md. This document describes styling, not an independent verification of those behaviors.

**Key Characteristics:**

- Deep teal navigation with clear active links.
- Paper-white panels on a pale working canvas.
- Bundled Manrope, compact labels and tabular figures.
- Flat sections, concise text and inspectable assessment details.

## Colors

Primary teal identifies actions and selection; neutral surfaces and readable ink support sustained review.

### Primary

- **Navigation teal**: the persistent sidebar.
- **Primary teal and deep teal**: action background and the inherited deep action variable.
- **Soft teal**: quiet supporting groups and hover surfaces.
- **Navigation hover, active and text**: explicit sidebar states independent of pale page surfaces.

### Neutral

- **Canvas and surface**: outer workspace and white working panels.
- **Ink and muted**: main content and supporting labels.
- **Line**: field boundaries, panel outlines and section dividers.
- **Focus**: the visible keyboard outline.
- **Dark canvas, surface, ink, muted, line, soft and accent**: implemented dark-theme overrides. The sidebar and filled primary actions retain their explicit teal treatments.

Semantic error, success and warning styles remain in the evidence components. They convey status alongside text and are not additional brand accents.

## Typography

Manrope is loaded locally from `frontend/src/assets/fonts/manrope-latin-variable.woff2` with a 200–800 weight range and swap loading. Segoe UI and sans-serif are fallbacks. The source applies tabular numerals throughout the workspace. Font verification and rendered review evidence belong in the redesign handoff, not in token values.

- **Headline**: page headings; reduces to 24px at the workspace small breakpoint. The login heading has its own 26px size.
- **Title**: section headings. Subsection headings use 15px with the same weight and leading.
- **Body**: working prose and general controls.
- **Label**: primary actions and field labels; navigation uses 13px at weight 500.
- **Table**: record cells; headers use 12px and weight 600, with sentence case and no extra letter spacing.

Toolbar and breadcrumb text use 12px. Supporting evidence prose uses 13px with a maximum of 75ch. Preserve currency, denominator and status next to the figure they qualify. Existing technical JSON editors may retain monospace; do not expand that exception into general interface copy.

## Layout

The desktop shell retains expanded and compact navigation, a sticky toolbar, and a scrolling main area. The sidebar is 224px wide in expanded desktop mode and 76px in compact mode. The toolbar has a 76px minimum height. Content is centered within a 1600px maximum width with 32px top, 36px inline and 48px bottom padding.

At 1100px, content and toolbar inline padding become 24px and the toolbar subtitle hides. At 700px, content padding becomes 20px 16px 32px, the toolbar minimum height becomes 64px and account-name text hides. The inherited shell supplies overlay navigation on small screens. Wide requirement records scroll locally below 1050px; the outer workspace clips horizontal overflow.

Evidence forms and source review retain their local 900px and 600px grid breakpoints. Native source scrolling and zoom remain available. Application-entry inner sections are flat, divided regions. Detailed scoring configuration and assessment metadata use disclosures rather than competing with routine fields.

## Elevation & Depth

The workspace is flat. Shadow-bearing utility elements have their shadows removed, including on hover; backdrop blur and the previous verification hero decoration are disabled. Surface contrast, borders, heading scale and spacing define groups. Do not restore the historical gradients or floating-card effects described in the integration archive.

Reduced-motion preference removes workspace transitions and animation and resets smooth scrolling. Focus uses a three-pixel outline with a three-pixel offset; it is a state indicator rather than elevation.

## Shapes

Controls use the control radius; general rounded working panels use the panel radius. Tags and toolbar controls use the smaller tag radius. The bounded login frame uses the login radius. Flattened application sections have no inner rounded box. Keep field and section boundaries aligned rather than adding nested containers.

## Components

### Buttons

Primary actions use teal with white text and the control radius. The shared primary class has 10px by 16px padding, a 42px minimum height and label typography. Existing integration actions retain their compact 9px by 14px padding and 40px minimum height. The toolbar uses transparent controls with a soft surface on hover. Disabled buttons retain a not-allowed cursor; evidence action controls also reduce opacity. Keyboard focus uses the shared outline. Do not invent a universal primary hover shade: the late workspace action override fixes the filled teal background.

### Inputs / Fields

Evidence inputs use the surface, ink, line and control-radius tokens, with 10px padding and a 42px minimum height. Labels remain visible. The shared workspace applies readable placeholder color and the focus outline; login inputs have a 46px minimum height. Native file selectors have a bordered canvas-colored button. Preserve file uploads, checkboxes and their labels.

### Navigation

Keep the logo, real destinations, collapse control and mobile behavior. Links use pale text on deep teal, a 44px minimum height, and explicit hover and current-page states. Navigation SVGs remain compact functional cues. The toolbar contains language, theme, account and logout controls.

### Cards / Containers

Working panels use a white surface, line border, panel radius and section padding. Page headings are unboxed and separated from content by a divider. Application-entry sections use flat headings and dividers. Role information is a simple separated list; its existing placeholder content does not establish a new user-management feature.

### Evidence and assessment details

Keep evidence points and approval probability separate. Preserve financial tables, source references, ownership confirmation, review state and assessment history. Assessment details expose policy and technical metadata without repeating them across the primary reading area. Requirement editing emphasizes name and points; scoring policies remain available behind a disclosure.

## Do's and Don'ts

### Do:

- **Do** use the shared Manrope family and the implemented teal, surface and ink roles.
- **Do** use borders and spacing to divide flat working sections.
- **Do** keep functional controls, visible labels, file uploads and keyboard focus available.
- **Do** retain local table scrolling, tabular figures and inspectable evidence details.
- **Do** keep evidence points and approval probability visibly distinct.

### Don't:

- **Don't** add emoji, promotional illustration, decorative metric icons or redundant introductory copy.
- **Don't** reintroduce inconsistent interface fonts, nested ornamental cards or historical gradient heroes.
- **Don't** treat old navy/blue/gold integration guidance as the current visual authority.
- **Don't** describe an unspecified prior project as a user-selected benchmark.
- **Don't** claim unperformed browser, responsive, accessibility or backend checks.
