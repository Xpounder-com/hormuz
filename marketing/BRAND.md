# Hormuz brand system

Concept: **Controlled passage**. Preserve the existing name, three-bar mark, and established cream/sage website direction. Make the product boundary visually recognizable across pages and materials.

The public guide and downloadable assets live at `website/public/brand/` and the `/brand/` route. The reusable mark and route-line components are in `website/app/components/Brand.tsx`. Shared application styles are in `website/app/brand.css`.

## Reference lessons

The user-selected reference is https://www.designgud.co. Its expanded Brand identity example shows a consistent mark family across formats; the Icon system shows a recognizable motif adapted to surfaces; the Marketing site example connects hero, features, and pricing; the Component library uses clear contrast and one primary action within a billing card.

Applied to Hormuz: primary/reverse lockups and a small icon, consistent route motifs, distinct editorial and functional surfaces, a clear primary CTA, and the same identity in downloadable materials. No reference artwork, copy, customer proof, or brand marks are reused.

## Original key artwork

The `controlled-passage.webp` artwork was generated with ImageGen for this project. Prompt: a premium abstract studio photograph of layered paper-and-mineral terrain, two curved cream/sage banks framing a generous open channel, matte daylight, fine shadows, forest recesses, one muted lime route. No text, marks, maps, shipping, robots, chips, shields, people, or UI. This is a visual metaphor, not a product diagram.

The source PNG is retained in local private build artifacts. The public optimized artwork is part of the kit. `build-brand-assets.py` exports the existing mark with portable outlined Geist lettering; the social layout combines native vector type with that artwork.

## Maintenance

Run website build, typecheck, tests, and export verification after changes. Visually inspect the homepage, interior routes, resource downloads, contact form, and brand page on desktop/mobile. A design pass must preserve payment scope, lead acknowledgments, and opt-in analytics behavior.

Regenerate the kit from the reviewed public assets and update PDF/PPTX styles when the identity changes. Keep operational records, submissions, credentials, and private account information out of public brand files.
