# App-specific offer settings

One shared AWS template renders every application. Some applications need a
different label, sentence or version wording. Those settings are declared per
application in a repository file; they are not facts and do not belong in
Contentful.

## Ownership

| Source | Owns |
| --- | --- |
| Contentful catalog item | Application facts: `trademark`, `overview`, `keywords`, `usecases`, `relatedApps`, `summary`, `hasenterprise`, `entityId`, `targets`, `offerCodes` |
| `data/listing/templates/` | Offer structure and default copy |
| `data/listing/vendor.json` | Shared vendor statements |
| `data/listing/app-special-cases.json` | Per-application rendering flags and wording |
| `catalog.contentOverrides` | Last-resort literal override; reviewed per listing |

`hasenterprise` stays a fact: "a separately licensed commercial edition
exists". It must not decide how the open-source edition is named.

## File

`data/listing/app-special-cases.json`, schema version 1, keyed by catalog
application id:

```json
{
  "schemaVersion": 1,
  "apps": {
    "odoo": { "multiVersion": true },
    "mongodb": { "multiVersion": true, "editionLabel": "Community Server" },
    "redis": { "multiVersion": true, "editionLabel": "Open Source" },
    "graylog": { "editionLabel": "Open" }
  }
}
```

Supported slots (all optional):

| Slot | Type | Meaning | Default when absent |
| --- | --- | --- | --- |
| `editionLabel` | string | Name of the edition this offer provides. Empty string means no label at all | `Community Edition` when `hasenterprise` is true, otherwise empty |
| `pricingLead` | string | First sentence of the "What you pay for" section | `<trademark> <editionLabel>` when a label exists, otherwise `No separate license fee is charged for the application.` |
| `multiVersion` | boolean | Advertise version selection when the versions source has two or more values | `false` |

Do not add per-application slots that duplicate catalog facts. Add a slot only
when the shared template or version source cannot express the difference.

`multiVersion` is a curation flag, not a derived fact: adding a version to
`apps-verson.json` must not silently change published wording.

## Rendering contract

`render()` in `cli/offer.py` resolves the entry before formatting placeholders
and exposes an `editions` namespace:

- `label` / `labelSpaced` / `hasLabel`
- `pricingLead`

Templates reference those values and do not concatenate or branch on edition
names. The three edition-dependent strings in `data/listing/templates/aws-ami.json`
use this namespace instead of `catalog.hasenterprise`.

`multiVersion` is read directly by `render()`; it only enables
`{versions.shortChoices}` and the `... for selection` suffix when the versions
source provides at least two values. Values that name a commercial edition
(`enterprise` or a standalone `ee` token, such as `5.26-enterprise` or
`5.26-ee`) are excluded before this count because the offer provides the
open-source edition.

The resolved edition values for an application are part of `sourceHash`, so
editing its label or pricing sentence makes an existing reviewed plan stale.

## Validation

`mk offer build` fails when:

- the file has an unknown `schemaVersion`, a non-object entry or a wrong slot type;
- an entry uses an unsupported slot;
- a label has surrounding whitespace;
- the rendered offer still contains an unresolved placeholder or two consecutive
  spaces.

Entries for applications not yet in the catalog are allowed, so a new application
can be prepared before its catalog item exists. Remove the entry when an
application is retired.

## Out of scope

- Structural differences (extra sections, different order): use a separate
  template file whose `templateId` matches the catalog item, or extend the
  shared template. Do not fork the template for wording.
- New Contentful fields for presentation copy.
- `contentOverrides.LongDescription`: it desynchronises the review preview from
  the submitted content, because `preview.longDescriptionSections` is rendered
  before overrides are applied.
