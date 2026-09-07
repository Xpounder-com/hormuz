# Hormuz brand system

Concept: **Controlled passage**. Use the approved passage H, the existing Hormuz name, and the established cream/sage website direction. Forest is the primary icon treatment; signal is the alternate. Make the product boundary visually recognizable across pages and materials.

The public guide and downloadable assets live at `website/public/brand/` and the `/brand/` route. The reusable mark and route-line components are in `website/app/components/Brand.tsx`. Shared application styles are in `website/app/brand.css`.

## Reference lessons

The user-selected reference is https://www.designgud.co. Its expanded Brand identity example shows a consistent mark family across formats; the Icon system shows a recognizable motif adapted to surfaces; the Marketing site example connects hero, features, and pricing; the Component library uses clear contrast and one primary action within a billing card.

Applied to Hormuz: primary/reverse lockups and a small icon, consistent route motifs, distinct editorial and functional surfaces, a clear primary CTA, and the same identity in downloadable materials. No reference artwork, copy, customer proof, or brand marks are reused.

## Original key artwork

The `controlled-passage.webp` artwork was generated with ImageGen for this project. Prompt: a premium abstract studio photograph of layered paper-and-mineral terrain, two curved cream/sage banks framing a generous open channel, matte daylight, fine shadows, forest recesses, one muted lime route. No text, marks, maps, shipping, robots, chips, shields, people, or UI. This is a visual metaphor, not a product diagram.

The source PNG is retained in local private build artifacts. The public optimized artwork is part of the kit. `build-brand-assets.py` exports the approved passage H with portable outlined Geist lettering; the social layout combines native vector type with that artwork.

## Maintenance

Run website build, typecheck, tests, and export verification after changes. Visually inspect the homepage, interior routes, resource downloads, contact form, and brand page on desktop/mobile. A design pass must preserve payment scope, lead acknowledgments, and opt-in analytics behavior.

Regenerate the kit from the reviewed public assets and update PDF/PPTX styles when the identity changes. Keep operational records, submissions, credentials, and private account information out of public brand files.

## Passage H production source

The owner approved the open-H identity and pixel-size review on September 6, 2026. Two solid gate forms with unequal heights frame an open central passage. The geometry lives in `website/lib/brand-mark.json` and is shared by the React mark and asset builders. The dedicated 16 px geometry aligns upright edges to whole pixels and widens the passage.

ImageGen supplied exploratory open-H and diagonal-H concepts. The selected open H was redrawn as clean vector geometry for deterministic exports; texture and edge artifacts from the generated image were not carried into the production assets. No Designgud artwork or logo was copied.

Run `scripts/build-brand-assets.py`, then `node scripts/build-brand-icons.mjs`, regenerate the buyer PDFs and deck, and run `python3 scripts/package-brand-assets.py`. The authoring scripts need the documented local font and document dependencies. All final assets are committed for dependency-free publication builds.
