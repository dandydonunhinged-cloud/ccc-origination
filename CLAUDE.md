# CCC Origination — notes for Claude

Investor-loan origination system (FastAPI + SQLAlchemy + Jinja2) plus the DanDon
Media Studio pipeline. See `README.md` for the architecture and endpoints.

## Stack facts that shape UI work

- Server-rendered Jinja2 templates under `app/templates/`. No SPA, no bundler,
  no build step. Do not introduce Tailwind, React, or a CSS build.
- Two stylesheets, hand-written: `static/app.css` (broker, borrower, partner
  surfaces, all extending `shared/base.html`) and `static/studio/studio.css`
  (Studio, extending `studio/base.html`). Add rules there; avoid inline
  `style=""` in templates except for one-off spacing.
- Brand palette lives in `:root` of `static/app.css` (red `--c-red`, blue
  `--c-blue`, paper `--c-paper`, ink `--c-ink`). The marketing site under
  `static/mortgage/` uses the same red/white/blue. Reuse the tokens; don't add
  new hex values without adding a token.
- Tests: `python -m pytest tests -q`. Run them before committing template or
  route changes.

## Design skills: which one, when

None of these are vendored in the repo; install them from a trusted terminal
at project scope (they land in `.claude/skills/`), then reload Claude Code.

```bash
# Anthropic frontend-design: plan tokens/type/layout first, check the plan
# against the generic default, then build. Markdown only.
npx skills add anthropics/skills --skill frontend-design

# Vercel web-design-guidelines: pre-ship review of templates and CSS for
# accessibility, focus, forms, responsive behaviour. Markdown only.
npx skills add vercel-labs/agent-skills --skill web-design-guidelines

# TasteSkill: art direction + real-browser inspector (Node 22+, Chrome)
npx skills add https://github.com/Leonxlnx/taste-skill --skill "design-taste-frontend"

# UI/UX Pro Max: searchable styles, palettes, font pairs, UX rules (Python 3)
npx ui-ux-pro-max-cli@latest init --ai claude

# Impeccable: /impeccable audit|critique|polish|harden on existing UI
npx impeccable install --providers=claude --scope=project
```

The last three also publish Claude Code plugin marketplaces. To have them
install on open, put this in `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "taste-skill": { "source": { "source": "github", "repo": "Leonxlnx/taste-skill" } },
    "ui-ux-pro-max-skill": { "source": { "source": "github", "repo": "nextlevelbuilder/ui-ux-pro-max-skill" } },
    "impeccable": { "source": { "source": "github", "repo": "pbakaus/impeccable" } }
  },
  "enabledPlugins": {
    "taste-skill@taste-skill": true,
    "ui-ux-pro-max@ui-ux-pro-max-skill": true,
    "impeccable@impeccable": true
  }
}
```

Division of labour: UI/UX Pro Max or TasteSkill to pick a direction for a new
surface (one of them, not both); frontend-design while building; Impeccable to
critique and polish what exists; web-design-guidelines as the final review.
Until they are installed, the house rules below are the standard.

## House rules for this app's UI

Most screens here are tools people work in (pipeline, deal detail, Studio
board), not marketing pages. Precision and restraint beat expression.

- One memorable element per screen. Everything else quiet.
- No decorative gradients, glows, glassmorphism, or icons in tinted squares.
  The single red/white/blue `.bar-rwb` stripe is the brand mark; leave it.
- Borders, eyebrows, numbering and dividers must encode something. Drop the
  ALL-CAPS tracked eyebrow unless it labels a real category.
- Type: one family, a real scale. Body stays at 14 to 16px, line length under
  80 characters, headings sized by hierarchy, not by habit.
- Tables and forms are the product. Align numbers right, keep labels visible,
  show validation inline, make every action's name match its result.
- Keyboard focus visible, reduced motion respected, contrast at least AA.
  Works at 360px wide with a 16px gutter and no horizontal scroll.
- Motion only in response to the user's action, and only to show what changed.
- Copy: plain verbs, sentence case, errors say what happened and what to do.

Before calling a UI change done, open the page in a browser (Playwright is
available in cloud sessions) at phone and desktop widths and look at it.
